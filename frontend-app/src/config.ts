// Configuration injected at deploy time: the web deploy workflow replaces the
// {{...}} placeholders in the built bundle with values read from the webui
// stack outputs. For local dev, set VITE_* in a .env.local file instead.
export const config = {
  userPoolId: import.meta.env.VITE_USER_POOL_ID || '{{USER_POOL_ID}}',
  clientId: import.meta.env.VITE_CLIENT_ID || '{{CLIENT_ID}}',
  cognitoDomain: import.meta.env.VITE_COGNITO_DOMAIN || '{{COGNITO_DOMAIN}}',
  apiUrl: import.meta.env.VITE_API_URL || '{{API_URL}}',
  region: 'us-east-2',
};
