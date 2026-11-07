"""
Generic CRUD handler for DynamoDB tables
Supports configurable table operations with validation
"""
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List, Callable
from botocore.exceptions import ClientError
from .logger import CustomLogger
from .dynamodb_helper import dynamodb_json

logger = CustomLogger()


class TableConfig:
    """Configuration for a DynamoDB table"""

    def __init__(
        self,
        table_name: str,
        table_resource: Any,
        primary_key: str = 'id',
        required_fields: List[str] = None,
        optional_fields: List[str] = None,
        validators: Dict[str, Callable] = None,
        unique_constraints: List[List[str]] = None,
        indexes: Dict[str, str] = None,
        auto_uppercase_fields: List[str] = None,
        read_only_fields: List[str] = None
    ):
        """
        Initialize table configuration

        Args:
            table_name: Name of the DynamoDB table
            table_resource: boto3 table resource
            primary_key: Name of the primary key field
            required_fields: List of required field names
            optional_fields: List of optional field names
            validators: Dict of field_name -> validation_function
            unique_constraints: List of field combinations that must be unique
            indexes: Dict mapping constraint fields to GSI names
            auto_uppercase_fields: Fields to automatically uppercase
            read_only_fields: Fields that cannot be modified after creation
        """
        self.table_name = table_name
        self.table_resource = table_resource
        self.primary_key = primary_key
        self.required_fields = required_fields or []
        self.optional_fields = optional_fields or []
        self.validators = validators or {}
        self.unique_constraints = unique_constraints or []
        self.indexes = indexes or {}
        self.auto_uppercase_fields = auto_uppercase_fields or []
        self.read_only_fields = read_only_fields or []


class CRUDHandler:
    """Generic CRUD operations handler for DynamoDB tables"""

    def __init__(self, config: TableConfig):
        """Initialize handler with table configuration"""
        self.config = config
        self.table = config.table_resource

    def validate_data(self, data: Dict[str, Any], is_update: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Validate data against table configuration

        Args:
            data: Data to validate
            is_update: Whether this is an update operation

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check required fields (skip for updates as they may be partial)
        if not is_update:
            for field in self.config.required_fields:
                if field not in data or not data[field]:
                    return False, f"Missing required field: {field}"

        # Validate each field present in data
        for field, value in data.items():
            # Skip validation for None/empty optional fields
            if field in self.config.optional_fields and not value:
                continue

            # Run custom validator if exists
            if field in self.config.validators:
                is_valid, error = self.config.validators[field](value)
                if not is_valid:
                    return False, error

        return True, None

    def prepare_item(self, data: Dict[str, Any], user_id: str, is_update: bool = False) -> Dict[str, Any]:
        """
        Prepare item for DynamoDB insertion/update

        Args:
            data: Raw data
            user_id: User performing the operation
            is_update: Whether this is an update operation

        Returns:
            Prepared item dictionary
        """
        timestamp = datetime.utcnow().isoformat()

        # Start with provided data
        item = {}

        # Add/update only allowed fields
        all_allowed = set(self.config.required_fields + self.config.optional_fields)
        for field in all_allowed:
            if field in data:
                value = data[field]

                # Auto-uppercase if configured
                if field in self.config.auto_uppercase_fields and isinstance(value, str):
                    value = value.strip().upper()
                elif isinstance(value, str):
                    value = value.strip()

                item[field] = value

        # Add metadata
        if is_update:
            item['updatedAt'] = timestamp
            item['updatedBy'] = user_id
        else:
            # Only generate UUID for primary key if not already provided
            # This allows tables to use user-provided values as primary keys
            if self.config.primary_key not in item:
                item[self.config.primary_key] = str(uuid.uuid4())
            item['createdAt'] = timestamp
            item['updatedAt'] = timestamp
            item['createdBy'] = user_id

        return item

    def check_unique_constraint(
        self,
        item: Dict[str, Any],
        exclude_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if item violates unique constraints

        Args:
            item: Item to check
            exclude_id: ID to exclude from check (for updates)

        Returns:
            Tuple of (is_duplicate, error_message)
        """
        for constraint_fields in self.config.unique_constraints:
            # Get constraint values from item
            constraint_values = {field: item.get(field) for field in constraint_fields}

            # Skip if any constraint field is missing
            if not all(constraint_values.values()):
                continue

            # Find the appropriate index
            constraint_key = tuple(sorted(constraint_fields))
            if constraint_key not in self.config.indexes:
                logger.log('WARNING', 'No index configured for constraint', fields=constraint_fields)
                continue

            index_name = self.config.indexes[constraint_key]

            # Build query parameters
            # For simplicity, we'll query on the first field and filter on others
            primary_field = constraint_fields[0]
            primary_value = constraint_values[primary_field]

            try:
                query_params = {
                    'IndexName': index_name,
                    'KeyConditionExpression': f'{primary_field} = :val0',
                    'ExpressionAttributeValues': {':val0': primary_value}
                }

                # Add filters for other fields
                if len(constraint_fields) > 1:
                    filter_expressions = []
                    for i, field in enumerate(constraint_fields[1:], start=1):
                        # Handle reserved words
                        attr_name = f'#{field}' if field in ['state', 'status'] else field
                        filter_expressions.append(f'{attr_name} = :val{i}')
                        query_params['ExpressionAttributeValues'][f':val{i}'] = constraint_values[field]

                        # Add expression attribute names for reserved words
                        if field in ['state', 'status']:
                            if 'ExpressionAttributeNames' not in query_params:
                                query_params['ExpressionAttributeNames'] = {}
                            query_params['ExpressionAttributeNames'][f'#{field}'] = field

                    query_params['FilterExpression'] = ' AND '.join(filter_expressions)

                # Exclude current record if updating
                if exclude_id:
                    if 'FilterExpression' in query_params:
                        query_params['FilterExpression'] += f' AND {self.config.primary_key} <> :exclude_id'
                    else:
                        query_params['FilterExpression'] = f'{self.config.primary_key} <> :exclude_id'
                    query_params['ExpressionAttributeValues'][':exclude_id'] = exclude_id

                response = self.table.query(**query_params)

                if response['Items']:
                    field_str = ' and '.join(constraint_fields)
                    return True, f"Record already exists with same {field_str}"

            except ClientError as e:
                logger.log('ERROR', 'Error checking unique constraint',
                          fields=constraint_fields, error=str(e))
                raise e

        return False, None

    def create(self, data: Dict[str, Any], user_id: str) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Create a new item

        Args:
            data: Item data
            user_id: User creating the item

        Returns:
            Tuple of (created_item, error_message)
        """
        try:
            # Validate
            is_valid, error = self.validate_data(data, is_update=False)
            if not is_valid:
                return None, error

            # Prepare item
            item = self.prepare_item(data, user_id, is_update=False)

            # Check unique constraints
            is_duplicate, error = self.check_unique_constraint(item)
            if is_duplicate:
                return None, error

            # Convert to DynamoDB format
            dynamo_item = dynamodb_json(item)

            # Insert
            self.table.put_item(Item=dynamo_item)

            logger.log('INFO', 'Item created',
                      table=self.config.table_name,
                      item_id=item[self.config.primary_key],
                      user_id=user_id)

            return item, None

        except ClientError as e:
            logger.log('ERROR', 'DynamoDB error creating item',
                      table=self.config.table_name, error=str(e))
            raise e
        except Exception as e:
            logger.log('ERROR', 'Unexpected error creating item',
                      table=self.config.table_name, error=str(e))
            raise e

    def get_one(self, item_id: str) -> Optional[Dict]:
        """
        Get a single item by ID

        Args:
            item_id: Item ID

        Returns:
            Item dict or None if not found
        """
        try:
            response = self.table.get_item(
                Key={self.config.primary_key: item_id}
            )

            if 'Item' not in response:
                return None

            return response['Item']

        except ClientError as e:
            logger.log('ERROR', 'DynamoDB error getting item',
                      table=self.config.table_name, item_id=item_id, error=str(e))
            raise e

    def get_all(self, filters: Dict[str, Any] = None) -> List[Dict]:
        """
        Get all items, optionally filtered

        Args:
            filters: Optional filters to apply

        Returns:
            List of items
        """
        try:
            response = self.table.scan()
            items = response['Items']

            # Handle pagination
            while 'LastEvaluatedKey' in response:
                response = self.table.scan(
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
                items.extend(response['Items'])

            # Apply client-side filters if needed
            if filters:
                filtered_items = []
                for item in items:
                    matches = True
                    for key, value in filters.items():
                        if item.get(key) != value:
                            matches = False
                            break
                    if matches:
                        filtered_items.append(item)
                items = filtered_items

            logger.log('INFO', 'Items retrieved',
                      table=self.config.table_name, count=len(items))

            return items

        except ClientError as e:
            logger.log('ERROR', 'DynamoDB error scanning table',
                      table=self.config.table_name, error=str(e))
            raise e

    def query_by_provider(self, provider_id: str) -> List[Dict]:
        """
        Query items by provider using the provider GSI.

        Args:
            provider_id: Provider ID to filter by

        Returns:
            List of items belonging to the provider
        """
        try:
            # Get the index name from config
            index_name = self.config.indexes.get('provider')
            if not index_name:
                logger.log('WARNING', 'No provider index configured, falling back to scan',
                          table=self.config.table_name)
                return self.get_all(filters={'provider': provider_id})

            # Query using the GSI
            from boto3.dynamodb.conditions import Key

            response = self.table.query(
                IndexName=index_name,
                KeyConditionExpression=Key('provider').eq(provider_id)
            )

            items = response.get('Items', [])

            # Handle pagination
            while 'LastEvaluatedKey' in response:
                response = self.table.query(
                    IndexName=index_name,
                    KeyConditionExpression=Key('provider').eq(provider_id),
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
                items.extend(response.get('Items', []))

            logger.log('INFO', 'Items retrieved by provider',
                      table=self.config.table_name, provider=provider_id, count=len(items))

            return items

        except ClientError as e:
            logger.log('ERROR', 'DynamoDB error querying by provider',
                      table=self.config.table_name, provider=provider_id, error=str(e))
            raise e

    def update(
        self,
        item_id: str,
        data: Dict[str, Any],
        user_id: str
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Update an existing item

        Args:
            item_id: Item ID
            data: Updated data
            user_id: User updating the item

        Returns:
            Tuple of (updated_item, error_message)
        """
        try:
            # Check if exists
            existing = self.get_one(item_id)
            if not existing:
                return None, "Record not found"

            # Validate
            is_valid, error = self.validate_data(data, is_update=True)
            if not is_valid:
                return None, error

            # Remove read-only fields from update data
            for field in self.config.read_only_fields:
                if field in data:
                    del data[field]

            # Prepare update item
            update_data = self.prepare_item(data, user_id, is_update=True)

            # Check unique constraints if any constraint fields are being updated
            constraint_check_needed = any(
                field in data
                for constraint in self.config.unique_constraints
                for field in constraint
            )

            if constraint_check_needed:
                # Merge with existing data for constraint check
                check_item = {**existing, **update_data}
                is_duplicate, error = self.check_unique_constraint(check_item, exclude_id=item_id)
                if is_duplicate:
                    return None, error

            # Build update expression
            update_expression_parts = []
            expression_attribute_names = {}
            expression_attribute_values = {}

            for i, (key, value) in enumerate(update_data.items()):
                if key == self.config.primary_key:
                    continue  # Skip primary key

                # Handle reserved words
                if key in ['state', 'status', 'name', 'timestamp', 'value']:
                    attr_name = f'#attr{i}'
                    expression_attribute_names[attr_name] = key
                    update_expression_parts.append(f'{attr_name} = :val{i}')
                else:
                    update_expression_parts.append(f'{key} = :val{i}')

                expression_attribute_values[f':val{i}'] = value

            update_expression = 'SET ' + ', '.join(update_expression_parts)

            # Perform update
            update_params = {
                'Key': {self.config.primary_key: item_id},
                'UpdateExpression': update_expression,
                'ExpressionAttributeValues': expression_attribute_values,
                'ReturnValues': 'ALL_NEW'
            }

            if expression_attribute_names:
                update_params['ExpressionAttributeNames'] = expression_attribute_names

            response = self.table.update_item(**update_params)

            updated_item = response['Attributes']

            logger.log('INFO', 'Item updated',
                      table=self.config.table_name,
                      item_id=item_id,
                      user_id=user_id)

            return updated_item, None

        except ClientError as e:
            logger.log('ERROR', 'DynamoDB error updating item',
                      table=self.config.table_name, item_id=item_id, error=str(e))
            raise e
        except Exception as e:
            logger.log('ERROR', 'Unexpected error updating item',
                      table=self.config.table_name, item_id=item_id, error=str(e))
            raise e

    def delete(self, item_id: str, user_id: str) -> Tuple[bool, Optional[str]]:
        """
        Delete an item

        Args:
            item_id: Item ID
            user_id: User deleting the item

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Check if exists
            existing = self.get_one(item_id)
            if not existing:
                return False, "Record not found"

            # Delete
            self.table.delete_item(
                Key={self.config.primary_key: item_id}
            )

            logger.log('INFO', 'Item deleted',
                      table=self.config.table_name,
                      item_id=item_id,
                      user_id=user_id)

            return True, None

        except ClientError as e:
            logger.log('ERROR', 'DynamoDB error deleting item',
                      table=self.config.table_name, item_id=item_id, error=str(e))
            raise e
