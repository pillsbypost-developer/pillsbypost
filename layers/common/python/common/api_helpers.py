"""
Shared helpers for admin API (API Gateway + Cognito authorizer) handlers.
"""


def build_cors_headers(origin, methods='GET,POST,PUT,DELETE,OPTIONS'):
    return {
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': methods,
        'Access-Control-Allow-Credentials': 'true',
    }


def get_user_context(event):
    """
    Identity from the Cognito authorizer claims.

    Returns {'user_id': sub, 'email': email, 'groups': [group names]}.
    """
    claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
    groups_raw = claims.get('cognito:groups', '')
    return {
        'user_id': claims.get('sub', 'unknown'),
        'email': claims.get('email', 'unknown'),
        'groups': groups_raw.split(',') if groups_raw else [],
    }


def is_admin(event):
    return 'Administrators' in get_user_context(event)['groups']
