import type { AuthProvider } from 'react-admin';
import type { AuthContextProps } from 'react-oidc-context';

// This creates a React Admin authProvider that uses the OIDC context
export const createAuthProvider = (auth: AuthContextProps): AuthProvider => ({
  login: async () => {
    // For OIDC, login is handled by signinRedirect
    await auth.signinRedirect();
    return Promise.resolve();
  },

  logout: async () => {
    // Sign out and redirect to Cognito logout
    await auth.signoutRedirect();
    return Promise.resolve();
  },

  checkAuth: async () => {
    // Check if user is authenticated
    if (auth.isAuthenticated && auth.user && !auth.user.expired) {
      return Promise.resolve();
    }
    
    // If not authenticated and not already loading, trigger login
    if (!auth.isLoading && !auth.activeNavigator) {
      await auth.signinRedirect();
    }
    
    return Promise.reject(new Error('Not authenticated'));
  },

  checkError: async (error) => {
    const status = error?.status;
    if (status === 401 || status === 403) {
      return Promise.reject(new Error('Unauthorized'));
    }
    return Promise.resolve();
  },

  getIdentity: async () => {
    if (!auth.user) {
      return Promise.reject(new Error('No user'));
    }

    const profile = auth.user.profile;
    
    return Promise.resolve({
      id: profile.sub || '',
      fullName: profile.name || profile.email || 'User',
      avatar: profile.picture,
    });
  },

  getPermissions: async () => {
    if (!auth.user) {
      return Promise.resolve([]);
    }

    // Cognito groups are in the 'cognito:groups' claim
    const groups = auth.user.profile['cognito:groups'] || [];
    return Promise.resolve(groups);
  },
});
