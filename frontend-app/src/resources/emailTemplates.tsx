import {
  List,
  Datagrid,
  TextField,
  EditButton,
  Create,
  Edit,
  SimpleForm,
  TextInput,
  required,
  DeleteWithConfirmButton,
  Toolbar,
  SaveButton,
  ListButton,
  TopToolbar,
  CreateButton,
  ExportButton,
} from 'react-admin';

// List view with custom actions
const EmailTemplatesListActions = () => (
  <TopToolbar>
    <CreateButton />
    <ExportButton />
  </TopToolbar>
);

export const EmailTemplatesList = () => (
  <List actions={<EmailTemplatesListActions />} sort={{ field: 'template_name', order: 'ASC' }}>
    <Datagrid rowClick="edit">
      <TextField source="template_id" label="Template ID" />
      <TextField source="template_name" label="Template Name" />
      <TextField source="subject" label="Subject" />
      <TextField source="category" label="Category" />
      <TextField source="description" label="Description" />
      <EditButton />
      <DeleteWithConfirmButton />
    </Datagrid>
  </List>
);

// Custom toolbar for forms
const EmailTemplatesToolbar = () => (
  <Toolbar>
    <SaveButton />
    <ListButton />
  </Toolbar>
);

export const EmailTemplatesEdit = () => (
  <Edit>
    <SimpleForm toolbar={<EmailTemplatesToolbar />}>
      <TextInput source="template_id" disabled fullWidth />
      <TextInput source="template_name" validate={[required()]} fullWidth />
      <TextInput source="subject" validate={[required()]} fullWidth />
      <TextInput source="body" validate={[required()]} multiline rows={8} fullWidth />
      <TextInput source="category" label="Category (Optional)" fullWidth />
      <TextInput source="description" label="Description (Optional)" multiline rows={3} fullWidth />
    </SimpleForm>
  </Edit>
);

export const EmailTemplatesCreate = () => (
  <Create>
    <SimpleForm toolbar={<EmailTemplatesToolbar />}>
      <TextInput source="template_id" validate={[required()]} fullWidth helperText="Unique identifier for this template" />
      <TextInput source="template_name" validate={[required()]} fullWidth />
      <TextInput source="subject" validate={[required()]} fullWidth />
      <TextInput source="body" validate={[required()]} multiline rows={8} fullWidth />
      <TextInput source="category" label="Category (Optional)" fullWidth />
      <TextInput source="description" label="Description (Optional)" multiline rows={3} fullWidth />
    </SimpleForm>
  </Create>
);
