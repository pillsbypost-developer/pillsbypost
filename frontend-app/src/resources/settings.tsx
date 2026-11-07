import {
  List,
  Datagrid,
  TextField,
  EditButton,
  Create,
  Edit,
  SimpleForm,
  TextInput,
  SelectInput,
  FormDataConsumer,
  required,
  DeleteWithConfirmButton,
  Toolbar,
  SaveButton,
  ListButton,
  TopToolbar,
  CreateButton,
  ExportButton,
} from 'react-admin';

// Settings with a fixed set of allowed values render as a select; everything
// else is free text. Values are always stored as strings.
const SELECT_SETTINGS: Record<string, { id: string; name: string }[]> = {
  fulfillment_mode: [
    { id: 'shipping', name: 'shipping - export orders to the shipping vendor' },
    { id: 'nectar', name: 'nectar - send fill requests to Honeybee Health' },
  ],
  honeybee_workflow: [
    { id: 'partner_requested', name: 'partner_requested - we request each fill' },
    { id: 'automatic', name: 'automatic - Honeybee fills on receipt' },
  ],
};

const SettingValueInput = () => (
  <FormDataConsumer>
    {({ formData }) => {
      const choices = SELECT_SETTINGS[formData?.config_key];
      return choices ? (
        <SelectInput source="value" label="Value" choices={choices} validate={[required()]} fullWidth />
      ) : (
        <TextInput source="value" label="Value" fullWidth />
      );
    }}
  </FormDataConsumer>
);

const SettingsListActions = () => (
  <TopToolbar>
    <CreateButton />
    <ExportButton />
  </TopToolbar>
);

export const SettingsList = () => (
  <List actions={<SettingsListActions />} sort={{ field: 'config_key', order: 'ASC' }}>
    <Datagrid rowClick="edit">
      <TextField source="config_key" label="Config Key" />
      <TextField source="value" label="Value" />
      <TextField source="description" label="Description" />
      <EditButton />
      <DeleteWithConfirmButton />
    </Datagrid>
  </List>
);

const SettingsToolbar = () => (
  <Toolbar>
    <SaveButton />
    <ListButton />
  </Toolbar>
);

export const SettingsEdit = () => (
  <Edit>
    <SimpleForm toolbar={<SettingsToolbar />}>
      <TextInput source="config_key" disabled fullWidth />
      <SettingValueInput />
      <TextInput source="description" fullWidth multiline />
    </SimpleForm>
  </Edit>
);

export const SettingsCreate = () => (
  <Create>
    <SimpleForm toolbar={<SettingsToolbar />}>
      <TextInput source="config_key" validate={[required()]} fullWidth helperText="Use snake_case (e.g., client_sender_email)" />
      <SettingValueInput />
      <TextInput source="description" fullWidth multiline />
    </SimpleForm>
  </Create>
);
