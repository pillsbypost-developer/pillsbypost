"""
Intake Worker Lambda (Pills By Post)

Consumes intake webhook messages from SQS (enqueued by intake-webhook) and
runs the slow IntakeQ orchestration: phone sync, duplicate detection,
eligibility (state / minimum age), payment-tier matching, the Converge
payment-link email, and recording the result on the intake claim record.

The intake-webhook lambda has already claimed the intake_id in the intakes
table (conditional put), so each message is processed at most once. This
worker finishes by updating that claim record with the full intake fields
and a final status:
  PROCESSED          - workflow completed (workflow_path = payment | admin_review)
  INELIGIBLE         - state not served / under minimum age / no DOB
  SKIPPED_NON_INTAKE - questionnaire was a follow-up/consent/reminder/research form
  VALIDATION_FAILED  - required client/intake fields missing (retry won't help)

Transient failures (IntakeQ fetch errors, unhandled exceptions) raise, so
SQS redelivers up to maxReceiveCount times and then parks the message in
the DLQ (alarmed).
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

import boto3

from common.logger import CustomLogger
from common.intakeq_client import IntakeQClient
from common.settings import Settings
from common.email_service import EmailService
from common.utils import calculate_age_from_dob, utc_now_iso

logger = CustomLogger()

# Environment variables (no fallbacks per Fail Fast policy)
ENV = os.environ['ENV']
INTAKES_TABLE = os.environ['INTAKES_TABLE']
SETTINGS_TABLE = os.environ['SETTINGS_TABLE']
EMAIL_TEMPLATES_TABLE = os.environ['EMAIL_TEMPLATES_TABLE']
EMAIL_SUPPRESSIONS_TABLE = os.environ['EMAIL_SUPPRESSIONS_TABLE']
PAYMENT_TIERS_TABLE = os.environ['PAYMENT_TIERS_TABLE']
ELIGIBILITY_RULES_TABLE = os.environ['ELIGIBILITY_RULES_TABLE']
INTAKEQ_API_SECRET = os.environ['INTAKEQ_API_SECRET']
INTAKEQ_API_DELAY = float(os.environ['INTAKEQ_API_DELAY'])

dynamodb = boto3.resource('dynamodb')
intakes_table = dynamodb.Table(INTAKES_TABLE)
email_templates_table = dynamodb.Table(EMAIL_TEMPLATES_TABLE)
payment_tiers_table = dynamodb.Table(PAYMENT_TIERS_TABLE)
eligibility_rules_table = dynamodb.Table(ELIGIBILITY_RULES_TABLE)

intakeq = IntakeQClient(logger, api_delay=INTAKEQ_API_DELAY)
settings = Settings(logger)
email_service = EmailService(logger, settings=settings, email_templates_table=email_templates_table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_main_intake_form(questionnaire_name, questionnaire_id=None):
    """
    Decide whether an intake is a main intake form (vs follow-up, consent,
    reminder, research questionnaires).

    If the `intake_questionnaire_ids` setting lists IntakeQ questionnaire ids,
    that list is authoritative. Otherwise fall back to name heuristics: names
    containing follow/reminder/research are skipped; 'consent' skips a form
    only when the name does not also say 'intake' (PBP's main form is
    "Medication Abortion Intake Form With Consents").
    """
    allowed_ids = settings.intake_questionnaire_ids
    if allowed_ids:
        return str(questionnaire_id) in allowed_ids
    if not questionnaire_name:
        return False
    name = questionnaire_name.lower()
    if any(keyword in name for keyword in ('follow', 'reminder', 'research')):
        return False
    if 'consent' in name and 'intake' not in name:
        return False
    return True


def validate_required_fields(client_data, intake_data):
    if not intake_data.get('DateCreated'):
        return False, "DateCreated missing"
    if not intake_data.get('ClientEmail'):
        return False, "ClientEmail missing"

    client_id = client_data.get('ClientId')
    try:
        if not client_id or int(client_id) <= 0:
            return False, "Invalid ClientId"
    except (TypeError, ValueError):
        return False, "Invalid ClientId"

    for field in ('FirstName', 'LastName', 'DateOfBirth', 'Email'):
        if not client_data.get(field):
            return False, f"{field} missing"
    return True, None


def extract_email_address(client_email, intake_email):
    for email in (client_email, intake_email):
        if email and '@' in email:
            return email.strip()
    return None


def get_comparephone_value(client_data):
    """COMPAREPHONE custom field value and FieldId, if present."""
    for field in client_data.get('CustomFields', []) or []:
        if field.get('Text') == 'COMPAREPHONE':
            return field.get('Value'), field.get('FieldId')
    return None, None


def build_full_client_update(client_data, updates):
    """
    IntakeQ requires the full client object on update (GET -> modify -> POST).
    PractitionerId is dropped unless explicitly set: it references a specific
    IntakeQ account and causes 400s when dev/prod accounts differ.
    """
    client_update = dict(client_data)
    if 'PractitionerId' in client_update and 'PractitionerId' not in updates:
        del client_update['PractitionerId']
    client_update.update(updates)
    return client_update


def sync_phone_fields(client_id, client_data):
    """Copy COMPAREPHONE to Phone/MobilePhone so the IntakeQ portal search finds it."""
    compare_phone, _ = get_comparephone_value(client_data)
    if not compare_phone:
        return
    client_update = build_full_client_update(client_data, {'Phone': compare_phone,
                                                           'MobilePhone': compare_phone})
    logger.log('INFO', "Syncing phone fields from COMPAREPHONE", client_id=client_id)
    if intakeq.update_client(client_update):
        client_data['Phone'] = compare_phone
        client_data['MobilePhone'] = compare_phone
    else:
        logger.log('ERROR', "Failed to sync phone fields", client_id=client_id)


def check_for_duplicates(client_data):
    """
    Tag DUPLICATE when an older client (lower ClientId) matches by email,
    COMPAREPHONE custom field, or full name. Returns True if tagged.
    """
    client_id = client_data.get('ClientId')
    email = (client_data.get('Email') or '').strip()
    full_name = f"{(client_data.get('FirstName') or '').strip()} {(client_data.get('LastName') or '').strip()}".strip()

    def is_older_client(match_id):
        try:
            return match_id is not None and int(match_id) < int(client_id)
        except (ValueError, TypeError):
            return False

    def tag_if_older(matches, kind):
        for match in matches:
            if is_older_client(match.get('ClientId')):
                intakeq.update_client_tags(client_id, "DUPLICATE", remove=False)
                logger.log('INFO', f"Tagged as {kind} duplicate",
                           client_id=client_id, older_client_id=match.get('ClientId'))
                return True
        return False

    if email:
        try:
            if tag_if_older(intakeq.search_clients_by_email(email), 'email'):
                return True
        except Exception as e:
            logger.log('ERROR', "Error checking email duplicates", error=str(e))

    compare_phone, field_id = get_comparephone_value(client_data)
    if compare_phone and field_id:
        try:
            if tag_if_older(intakeq.search_clients_by_custom_field(field_id, compare_phone), 'phone'):
                return True
        except Exception as e:
            logger.log('ERROR', "Error checking phone duplicates", error=str(e))

    if full_name:
        try:
            if tag_if_older(intakeq.search_clients_by_name(full_name), 'name'):
                return True
        except Exception as e:
            logger.log('ERROR', "Error checking name duplicates", error=str(e))

    return False


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def check_eligibility(state, age):
    """
    Look up the eligibility rule for the client's state.

    Returns (eligible: bool, reason: str). reason is 'eligible' when served.
    """
    if age is None:
        return False, "no_dob"
    if not state:
        return False, "no_state"

    response = eligibility_rules_table.get_item(Key={'state': state.upper()})
    rule = response.get('Item')
    if not rule:
        return False, f"state_not_configured:{state.upper()}"

    served = rule.get('served')
    if isinstance(served, str):
        served = served.strip().lower() == 'true'
    if not served:
        return False, f"state_not_served:{state.upper()}"

    min_age = rule.get('min_age')
    if min_age is not None and age < int(min_age):
        return False, f"under_min_age:{int(min_age)}"

    return True, "eligible"


# ---------------------------------------------------------------------------
# Payment tiers
# ---------------------------------------------------------------------------

def get_active_payment_tiers():
    response = payment_tiers_table.scan()
    items = list(response.get('Items', []))
    while 'LastEvaluatedKey' in response:
        response = payment_tiers_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))
    active = []
    for item in items:
        flag = item.get('active', True)
        if isinstance(flag, str):
            flag = flag.strip().lower() == 'true'
        if flag:
            active.append(item)
    return active


def match_payment_tier(answers_text, tiers):
    """
    Match each tier's intake_answer_pattern (regex, case-insensitive) against
    the joined intake answers. Returns (tier, error) where error is
    'no_match' or 'multiple_matches:<keys>' and tier is None on error.
    """
    if not answers_text:
        return None, "no_match"
    matches = []
    for tier in tiers:
        pattern = tier.get('intake_answer_pattern')
        if not pattern:
            continue
        try:
            if re.search(pattern, answers_text, re.IGNORECASE):
                matches.append(tier)
        except re.error as e:
            logger.log('ERROR', "Invalid intake_answer_pattern regex",
                       tier_key=tier.get('tier_key'), error=str(e))
    if not matches:
        return None, "no_match"
    if len(matches) > 1:
        keys = ','.join(str(t.get('tier_key')) for t in matches)
        return None, f"multiple_matches:{keys}"
    return matches[0], None


def build_payment_url(payment_link, client_id, email):
    """
    Append the IntakeQ ClientId (as Converge customer id and invoice number)
    and the email to the tier's Converge payment link so payments reconcile.

    Parameter names follow Converge hosted-payment conventions; confirm
    against the client's Converge admin (decision D8 - pending).
    """
    if not payment_link:
        return None
    separator = '&' if '?' in payment_link else '?'
    params = (f"ssl_customer_id={quote(str(client_id))}"
              f"&ssl_invoice_number={quote(str(client_id))}"
              f"&ssl_email={quote(email or '')}")
    return f"{payment_link}{separator}{params}"


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def process_intake_workflow(client_data, intake_data, age):
    """
    Eligibility -> payment tier -> payment email.

    Returns a dict of fields to merge into the claim record, always including
    'status' and 'workflow_path'.
    """
    client_id = client_data.get('ClientId')
    state = (client_data.get('StateShort') or '').upper() or None

    eligible, reason = check_eligibility(state, age)
    if not eligible:
        intakeq.update_client_tags(client_id, "INELIGIBLE", remove=False)
        email_service.send_client_ineligible_email(client_data)
        logger.log('INFO', "Client ineligible", client_id=client_id, state=state, age=age, reason=reason)
        return {
            'status': 'INELIGIBLE',
            'workflow_path': 'ineligible',
            'eligibility_result': reason,
            'email_template_sent': 'ineligible',
        }

    questions = intake_data.get('Questions', []) or []
    answers_text = ' '.join(str(q.get('Answer')) for q in questions if q.get('Answer'))
    logger.log('DEBUG', "Joined intake answers for tier matching", answers_text=answers_text)

    tier, error = match_payment_tier(answers_text, get_active_payment_tiers())
    if tier is None:
        intakeq.update_client_tags(client_id, "ADMIN", remove=False)
        email_service.send_admin_review_email(client_data, f"Payment tier could not be determined ({error})")
        logger.log('INFO', "Admin review - payment tier not determined",
                   client_id=client_id, reason=error)
        return {
            'status': 'PROCESSED',
            'workflow_path': 'admin_review',
            'eligibility_result': reason,
            'admin_review_reason': error,
        }

    tier_key = tier.get('tier_key')
    amount = tier.get('amount')
    payment_url = build_payment_url(tier.get('payment_link'), client_id, client_data.get('Email'))

    client_update = build_full_client_update(client_data, {
        'AdditionalInformation': f"PAYMENT LINK:\n{payment_url}\n",
    })
    if not intakeq.update_client(client_update):
        logger.log('ERROR', "Failed to write payment link to client record", client_id=client_id)

    intakeq.update_client_tags(client_id, "AWAITING-PAYMENT", remove=False)
    email_service.send_client_payment_email(client_data, payment_url, amount)

    logger.log('INFO', "Payment tier matched", client_id=client_id, tier_key=tier_key, amount=amount,
               pharmacy_choice=tier.get('pharmacy_choice'))
    return {
        'status': 'PROCESSED',
        'workflow_path': 'payment',
        'eligibility_result': reason,
        'payment_tier': tier_key,
        'payment_amount': amount,
        'pharmacy_choice': tier.get('pharmacy_choice'),
        'payment_url': payment_url,
        'email_template_sent': 'payment',
    }


def check_stale_account_reactivation(client_id, client_data):
    """
    Admin alert when a client whose last activity is older than
    stale_account_months submits a new intake. Non-blocking.
    """
    try:
        stale_months = settings.stale_account_months
        if stale_months is None:
            return
        last_activity_ts = client_data.get('LastActivityDate')
        if not last_activity_ts:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_months * 30)
        last_activity = datetime.fromtimestamp(int(last_activity_ts) / 1000, tz=timezone.utc)
        if last_activity < cutoff:
            logger.log('INFO', "Stale account reactivated - sending admin alert",
                       client_id=client_id, last_activity_date=last_activity.strftime('%Y-%m-%d'))
            email_service.send_stale_reactivation_alert(client_id)
    except Exception as e:
        logger.log('ERROR', "Error checking stale account reactivation", client_id=client_id, error=str(e))


def update_claim_record(intake_id, fields):
    """
    Update the claim record written by intake-webhook. Uses attribute-name
    placeholders for every field (several are DynamoDB reserved words) and
    converts floats to Decimal.
    """
    set_parts, attr_names, attr_values = [], {}, {}
    for i, (key, value) in enumerate(fields.items()):
        set_parts.append(f'#f{i} = :v{i}')
        attr_names[f'#f{i}'] = key
        if isinstance(value, float):
            value = Decimal(str(value))
        attr_values[f':v{i}'] = value

    intakes_table.update_item(
        Key={'intake_id': str(intake_id)},
        UpdateExpression='SET ' + ', '.join(set_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )


def process_intake_message(body):
    """
    Process one intake webhook message (the original webhook payload).

    Raises on transient failures (IntakeQ fetch errors) so SQS redelivers.
    Permanent conditions update the claim record and return.
    """
    client_id = body.get('ClientId')
    intake_id = body.get('IntakeId')
    event_type = body.get('EventType')

    logger.log('INFO', "Processing intake message", intake_id=intake_id, client_id=client_id)

    client_data = intakeq.get_client_by_id(client_id)
    intake_data = intakeq.get_intake_by_id(intake_id)
    if not client_data or not intake_data:
        raise RuntimeError(f"Failed to retrieve IntakeQ data for intake {intake_id}")

    logger.log('DEBUG', "Full client data from IntakeQ", client_data=client_data)
    logger.log('DEBUG', "Full intake data from IntakeQ", intake_data=intake_data)

    questionnaire_name = intake_data.get('QuestionnaireName', '')
    if not is_main_intake_form(questionnaire_name, intake_data.get('QuestionnaireId')):
        logger.log('INFO', "Skipping non-intake form", questionnaire_name=questionnaire_name)
        update_claim_record(intake_id, {
            'status': 'SKIPPED_NON_INTAKE',
            'questionnaire_name': questionnaire_name,
            'event_type': event_type,
            'processed_at': utc_now_iso(),
        })
        return

    is_valid, validation_error = validate_required_fields(client_data, intake_data)
    if not is_valid:
        logger.log('ERROR', "Validation failed", intake_id=intake_id, error=validation_error)
        update_claim_record(intake_id, {
            'status': 'VALIDATION_FAILED',
            'validation_error': validation_error,
            'questionnaire_name': questionnaire_name,
            'event_type': event_type,
            'processed_at': utc_now_iso(),
        })
        return

    sync_phone_fields(client_id, client_data)
    is_duplicate = check_for_duplicates(client_data)

    email = extract_email_address(client_data.get('Email'), intake_data.get('ClientEmail'))
    age = calculate_age_from_dob(client_data.get('DateOfBirth'))

    result = process_intake_workflow(client_data, intake_data, age)

    update_claim_record(intake_id, {
        'questionnaire_name': questionnaire_name,
        'event_type': event_type,
        'client_email': email,
        'age': age,
        'state': (client_data.get('StateShort') or '').upper() or None,
        'is_duplicate': is_duplicate,
        'processed_at': utc_now_iso(),
        **result,
    })

    check_stale_account_reactivation(client_id, client_data)

    logger.log('INFO', "Successfully processed intake", client_id=client_id, intake_id=intake_id,
               status=result['status'], workflow_path=result['workflow_path'], age=age)


def lambda_handler(event, context):
    """
    SQS entry point. BatchSize is 1, so a raised exception fails only this
    message; SQS redelivers up to maxReceiveCount, then moves it to the DLQ.
    """
    for record in event['Records']:
        process_intake_message(json.loads(record['body']))
    return {'statusCode': 200}
