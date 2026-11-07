import { User } from 'oidc-client-ts';
import { config } from '../config';

/**
 * Get JWT token from OIDC storage
 * @returns JWT token string or null if not found
 */
export const getJwtToken = (): string | null => {
  try {
    // The token is stored in localStorage by react-oidc-context
    const oidcStorageKey = `oidc.user:https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}:${config.clientId}`;
    const oidcStorage = localStorage.getItem(oidcStorageKey);

    if (!oidcStorage) {
      return null;
    }

    const user = User.fromStorageString(oidcStorage);
    return user?.id_token || null;
  } catch (e) {
    console.error('Error getting token:', e);
    return null;
  }
};
