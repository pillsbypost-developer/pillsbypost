import type { ReactNode } from 'react';
import { Admin, Resource } from 'react-admin';
import { useAuth } from 'react-oidc-context';
import { createAuthProvider } from './authProvider';
import { dataProvider } from './dataProvider';
import { OrdersList, OrdersShow } from './resources/orders';
import { IntakesList, IntakesShow } from './resources/intakes';
import { PaymentTiersList, PaymentTiersEdit, PaymentTiersCreate } from './resources/paymentTiers';
import { EligibilityRulesList, EligibilityRulesEdit, EligibilityRulesCreate } from './resources/eligibilityRules';
import { EmailTemplatesList, EmailTemplatesEdit, EmailTemplatesCreate } from './resources/emailTemplates';
import { EmailSuppressionsList, EmailSuppressionsShow, EmailSuppressionsCreate } from './resources/emailSuppressions';
import { TrackingList, TrackingEdit, TrackingCreate } from './resources/tracking';
import { TrackingJobsList, TrackingJobsShow } from './resources/trackingJobs';
import { HoneybeeEventsList, HoneybeeEventsShow } from './resources/honeybeeEvents';
import { SettingsList, SettingsEdit, SettingsCreate } from './resources/settings';

const Centered = ({ children }: { children: ReactNode }) => (
  <div style={{
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    alignItems: 'center',
    height: '100vh',
    gap: '1rem',
  }}>
    {children}
  </div>
);

const App = () => {
  const auth = useAuth();

  if (auth.isLoading) {
    return <Centered><div>Loading authentication...</div></Centered>;
  }

  if (auth.error) {
    return (
      <Centered>
        <div>Authentication Error: {auth.error.message}</div>
        <button onClick={() => auth.signinRedirect()}>Try Again</button>
      </Centered>
    );
  }

  if (!auth.isAuthenticated) {
    auth.signinRedirect();
    return <Centered><div>Redirecting to sign in...</div></Centered>;
  }

  // Only members of the Cognito "Administrators" group may use the portal.
  const userGroups = (auth.user?.profile?.['cognito:groups'] as string[]) || [];
  const isAdmin = userGroups.includes('Administrators');

  if (!isAdmin) {
    return (
      <Centered>
        <div>You do not have access to this application.</div>
        <div style={{ color: '#666', fontSize: '14px' }}>
          Contact your administrator if you believe this is an error.
        </div>
        <button onClick={() => auth.signoutRedirect()}>Sign Out</button>
      </Centered>
    );
  }

  const authProvider = createAuthProvider(auth);

  return (
    <Admin
      authProvider={authProvider}
      dataProvider={dataProvider}
      requireAuth
      title="Pills By Post Admin"
    >
      <Resource name="orders" list={OrdersList} show={OrdersShow} options={{ label: 'Orders' }} />
      <Resource name="intakes" list={IntakesList} show={IntakesShow} options={{ label: 'Intakes' }} />
      <Resource
        name="payment-tiers"
        list={PaymentTiersList}
        edit={PaymentTiersEdit}
        create={PaymentTiersCreate}
        options={{ label: 'Payment Tiers' }}
      />
      <Resource
        name="eligibility-rules"
        list={EligibilityRulesList}
        edit={EligibilityRulesEdit}
        create={EligibilityRulesCreate}
        options={{ label: 'Eligibility Rules' }}
      />
      <Resource
        name="email-templates"
        list={EmailTemplatesList}
        edit={EmailTemplatesEdit}
        create={EmailTemplatesCreate}
        options={{ label: 'Email Templates' }}
      />
      <Resource
        name="email-suppressions"
        list={EmailSuppressionsList}
        show={EmailSuppressionsShow}
        create={EmailSuppressionsCreate}
        options={{ label: 'Email Suppressions' }}
      />
      <Resource
        name="tracking"
        list={TrackingList}
        edit={TrackingEdit}
        create={TrackingCreate}
        options={{ label: 'Tracking' }}
      />
      <Resource
        name="tracking-jobs"
        list={TrackingJobsList}
        show={TrackingJobsShow}
        options={{ label: 'Tracking Jobs' }}
      />
      <Resource
        name="honeybee-events"
        list={HoneybeeEventsList}
        show={HoneybeeEventsShow}
        options={{ label: 'Honeybee Events' }}
      />
      <Resource
        name="settings"
        list={SettingsList}
        edit={SettingsEdit}
        create={SettingsCreate}
        options={{ label: 'Settings' }}
      />
    </Admin>
  );
};

export default App;
