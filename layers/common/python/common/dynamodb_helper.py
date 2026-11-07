import json
from decimal import Decimal


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles Decimal types from DynamoDB."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)


def dynamodb_json(raw):
    """
    Convert Python objects to DynamoDB-compatible JSON format
    Automatically converts floats to Decimal objects

    Args:
        raw: Any JSON-serializable Python object

    Returns:
        A DynamoDB-compatible representation with floats converted to Decimal
    """
    return json.loads(json.dumps(raw, cls=DecimalEncoder), parse_float=Decimal)
