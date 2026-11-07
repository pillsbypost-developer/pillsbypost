import json
import os
from datetime import datetime, timezone

import boto3

from common.logger import CustomLogger

logger = CustomLogger()

EMAIL_SUPPRESSIONS_TABLE = os.environ['EMAIL_SUPPRESSIONS_TABLE']

dynamodb = boto3.resource('dynamodb')
suppressions_table = dynamodb.Table(EMAIL_SUPPRESSIONS_TABLE)


def lambda_handler(event, context):
    logger.log('INFO', "Received SNS notification for SES event")

    for record in event['Records']:
        sns_message = record['Sns']
        sns_message_id = sns_message['MessageId']
        ses_notification = json.loads(sns_message['Message'])

        notification_type = ses_notification.get('eventType')
        logger.log('INFO', "Processing SES notification", notification_type=notification_type)

        if notification_type == 'Bounce':
            _handle_bounce(ses_notification, sns_message_id)
        elif notification_type == 'Complaint':
            _handle_complaint(ses_notification, sns_message_id)
        else:
            logger.log('INFO', "Ignoring notification type", notification_type=notification_type)

    return {'statusCode': 200, 'body': 'OK'}


def _handle_bounce(notification, sns_message_id):
    bounce = notification['bounce']
    bounce_type = bounce.get('bounceType', '')
    bounce_sub_type = bounce.get('bounceSubType', '')
    source_email = notification.get('mail', {}).get('source', '')

    logger.log('INFO', "Processing bounce",
               bounce_type=bounce_type, bounce_sub_type=bounce_sub_type)

    for recipient in bounce.get('bouncedRecipients', []):
        email = recipient.get('emailAddress', '').lower().strip()
        if not email:
            continue

        if bounce_type == 'Permanent':
            logger.log('INFO', "Suppressing email due to permanent bounce", email=email)
            _write_suppression(
                email=email,
                reason='bounce',
                bounce_type=bounce_type,
                bounce_sub_type=bounce_sub_type,
                source_email=source_email,
                sns_message_id=sns_message_id,
                raw_notification=json.dumps(notification)
            )
        else:
            logger.log('INFO', "Transient bounce - not suppressing", email=email,
                       bounce_type=bounce_type, bounce_sub_type=bounce_sub_type)


def _handle_complaint(notification, sns_message_id):
    complaint = notification['complaint']
    source_email = notification.get('mail', {}).get('source', '')

    logger.log('INFO', "Processing complaint")

    for recipient in complaint.get('complainedRecipients', []):
        email = recipient.get('emailAddress', '').lower().strip()
        if not email:
            continue

        logger.log('INFO', "Suppressing email due to complaint", email=email)
        _write_suppression(
            email=email,
            reason='complaint',
            source_email=source_email,
            sns_message_id=sns_message_id,
            raw_notification=json.dumps(notification)
        )


def _write_suppression(email, reason, source_email, sns_message_id,
                       raw_notification, bounce_type=None, bounce_sub_type=None):
    item = {
        'email': email,
        'reason': reason,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_email': source_email,
        'sns_message_id': sns_message_id,
        'raw_notification': raw_notification,
    }

    if bounce_type:
        item['bounce_type'] = bounce_type
    if bounce_sub_type:
        item['bounce_sub_type'] = bounce_sub_type

    suppressions_table.put_item(Item=item)
    logger.log('INFO', "Suppression record written", email=email, reason=reason)
