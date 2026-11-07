import { WebStorageStateStore } from 'oidc-client-ts';
import { config } from './config';

export const oidcConfig = {
  authority: `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`,
  client_id: config.clientId,
  redirect_uri: `${window.location.origin}/auth-callback`,
  post_logout_redirect_uri: window.location.origin,

  // OAuth scopes
  scope: 'openid email profile aws.cognito.signin.user.admin',

  // Store user in localStorage for persistence across page refreshes
  userStore: new WebStorageStateStore({ store: window.localStorage }),

  // Automatic silent token renewal
  automaticSilentRenew: true,

  // Provide complete metadata including all endpoints
  // We need to include ALL endpoints when overriding metadata, not just end_session_endpoint
  metadata: {
    issuer: `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`,
    authorization_endpoint: `https://${config.cognitoDomain}/oauth2/authorize`,
    token_endpoint: `https://${config.cognitoDomain}/oauth2/token`,
    userinfo_endpoint: `https://${config.cognitoDomain}/oauth2/userInfo`,
    jwks_uri: `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}/.well-known/jwks.json`,
    // Include client_id and logout_uri in the endpoint URL
    end_session_endpoint: `https://${config.cognitoDomain}/logout?client_id=${config.clientId}&logout_uri=${encodeURIComponent(window.location.origin)}`,
  },

  // Handle the callback URL cleanup
  onSigninCallback: () => {
    // Remove the ?code= and ?state= from URL after successful login
    window.history.replaceState({}, document.title, window.location.pathname);
  },
};
