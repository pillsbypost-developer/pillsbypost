import {
  List,
  Datagrid,
  TextField,
  NumberField,
  BooleanField,
  UrlField,
  EditButton,
  Create,
  Edit,
  SimpleForm,
  TextInput,
  NumberInput,
  BooleanInput,
  SelectInput,
  required,
  minValue,
  regex,
  DeleteWithConfirmButton,
  Toolbar,
  SaveButton,
  ListButton,
  TopToolbar,
  CreateButton,
  ExportButton,
} from 'react-admin';

const PHARMACY_CHOICES = [
  { id: 'mail', name: 'mail - shipped / Nectar' },
  { id: 'local', name: 'local - patient\'s own pharmacy' },
];

const ListActions = () => (
  <TopToolbar>
    <CreateButton />
    <ExportButton />
  </TopToolbar>
);

export const PaymentTiersList = () => (
  <List actions={<ListActions />} sort={{ field: 'tier_key', order: 'ASC' }}>
    <Datagrid rowClick="edit">
      <TextField source="tier_key" label="Tier Key" />
      <TextField source="label" label="Label" />
      <TextField source="pharmacy_choice" label="Pharmacy" />
      <NumberField source="amount" label="Amount" options={{ style: 'currency', currency: 'USD' }} />
      <UrlField source="payment_link" label="Payment Link" target="_blank" />
      <TextField source="intake_answer_pattern" label="Intake Answer Pattern" />
      <BooleanField source="active" label="Active" />
      <EditButton />
      <DeleteWithConfirmButton />
    </Datagrid>
  </List>
);

const FormToolbar = () => (
  <Toolbar>
    <SaveButton />
    <ListButton />
  </Toolbar>
);

const TierFields = ({ isCreate }: { isCreate?: boolean }) => (
  <>
    <TextInput
      source="tier_key"
      label="Tier Key"
      disabled={!isCreate}
      validate={isCreate ? [required(), regex(/^[a-z0-9_]+$/, 'lowercase letters, digits and underscores only')] : []}
      helperText="Stable identifier, e.g. mail_150"
      fullWidth
    />
    <TextInput source="label" label="Label" validate={[required()]} fullWidth />
    <SelectInput source="pharmacy_choice" label="Pharmacy choice" choices={PHARMACY_CHOICES} validate={[required()]} fullWidth />
    <NumberInput source="amount" label="Amount (USD)" validate={[required(), minValue(0)]} fullWidth />
    <TextInput source="payment_link" label="Converge payment link" validate={[required()]} fullWidth helperText="Hosted payment page URL for this tier" />
    <TextInput
      source="intake_answer_pattern"
      label="Intake answer pattern"
      validate={[required()]}
      fullWidth
      multiline
      helperText="Text (or regex) matched against the intake form answers to select this tier"
    />
    <BooleanInput source="active" label="Active" defaultValue={true} />
  </>
);

export const PaymentTiersEdit = () => (
  <Edit>
    <SimpleForm toolbar={<FormToolbar />}>
      <TierFields />
    </SimpleForm>
  </Edit>
);

export const PaymentTiersCreate = () => (
  <Create>
    <SimpleForm toolbar={<FormToolbar />}>
      <TierFields isCreate />
    </SimpleForm>
  </Create>
);
