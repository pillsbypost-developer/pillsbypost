import {
  List,
  Datagrid,
  TextField,
  NumberField,
  BooleanField,
  EditButton,
  Create,
  Edit,
  SimpleForm,
  TextInput,
  NumberInput,
  BooleanInput,
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

const ListActions = () => (
  <TopToolbar>
    <CreateButton />
    <ExportButton />
  </TopToolbar>
);

export const EligibilityRulesList = () => (
  <List actions={<ListActions />} sort={{ field: 'state', order: 'ASC' }}>
    <Datagrid rowClick="edit">
      <TextField source="state" label="State" />
      <BooleanField source="served" label="Served" />
      <NumberField source="min_age" label="Minimum Age" />
      <TextField source="notes" label="Notes" />
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

const RuleFields = ({ isCreate }: { isCreate?: boolean }) => (
  <>
    <TextInput
      source="state"
      label="State"
      disabled={!isCreate}
      validate={isCreate ? [required(), regex(/^[A-Za-z]{2}$/, 'two-letter state code')] : []}
      parse={(v: string) => (v ? v.toUpperCase() : v)}
      helperText="Two-letter code, e.g. MI"
      fullWidth
    />
    <BooleanInput source="served" label="Served" defaultValue={true} />
    <NumberInput source="min_age" label="Minimum age" validate={[required(), minValue(0)]} fullWidth />
    <TextInput source="notes" label="Notes" multiline rows={3} fullWidth />
  </>
);

export const EligibilityRulesEdit = () => (
  <Edit>
    <SimpleForm toolbar={<FormToolbar />}>
      <RuleFields />
    </SimpleForm>
  </Edit>
);

export const EligibilityRulesCreate = () => (
  <Create>
    <SimpleForm toolbar={<FormToolbar />}>
      <RuleFields isCreate />
    </SimpleForm>
  </Create>
);
