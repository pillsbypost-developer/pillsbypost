import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { config } from './config';

export const OAuthCallback = () => {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleCallback = async () => {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('code');
      const errorParam = params.get('error');

      if (errorParam) {
        setError(params.get('error_description') || errorParam);
        return;
      }

      if (code) {
        try {
          // Exchange code for tokens
          const tokenEndpoint = `https://${config.cognitoDomain}/oauth2/token`;
          
          const response = await fetch(tokenEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({
              grant_type: 'authorization_code',
              client_id: config.clientId,
              code: code,
              redirect_uri: window.location.origin,
            }),
          });

          if (!response.ok) {
            const errorData = await response.text();
            console.error('Token exchange error:', errorData);
            throw new Error('Token exchange failed');
          }

          const tokens = await response.json();
          
          // Store tokens
          localStorage.setItem('id_token', tokens.id_token);
          localStorage.setItem('access_token', tokens.access_token);
          if (tokens.refresh_token) {
            localStorage.setItem('refresh_token', tokens.refresh_token);
          }

          // Redirect to main app
          navigate('/');
        } catch (err) {
          setError('Failed to complete login');
          console.error(err);
        }
      }
    };

    handleCallback();
  }, [navigate]);

  if (error) {
    return (
      <div style={{ padding: '40px', textAlign: 'center' }}>
        <h2>Login Error</h2>
        <p>{error}</p>
        <button onClick={() => navigate('/')}>Return to Login</button>
      </div>
    );
  }

  return (
    <div style={{ padding: '40px', textAlign: 'center' }}>
      <h2>Completing login...</h2>
    </div>
  );
};
