import {
  List,
  Datagrid,
  TextField,
  DateField,
  Create,
  SimpleForm,
  TextInput,
  required,
  email,
  DeleteWithConfirmButton,
  TopToolbar,
  CreateButton,
  ExportButton,
  Show,
  SimpleShowLayout,
  useRecordContext,
} from 'react-admin';
import { Chip } from '@mui/material';

const ReasonField = () => {
  const record = useRecordContext();
  if (!record) return null;

  const colorMap: Record<string, 'warning' | 'error' | 'default'> = {
    bounce: 'warning',
    complaint: 'error',
    manual: 'default',
  };

  return (
    <Chip
      label={record.reason}
      color={colorMap[record.reason] || 'default'}
      size="small"
    />
  );
};

const SuppressionsListActions = () => (
  <TopToolbar>
    <CreateButton />
    <ExportButton />
  </TopToolbar>
);

export const EmailSuppressionsList = () => (
  <List actions={<SuppressionsListActions />} sort={{ field: 'created_at', order: 'DESC' }}>
    <Datagrid rowClick="show">
      <TextField source="email" />
      <ReasonField />
      <TextField source="bounce_type" label="Bounce Type" />
      <DateField source="created_at" label="Created" showTime />
      <DeleteWithConfirmButton />
    </Datagrid>
  </List>
);

export const EmailSuppressionsShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="email" />
      <TextField source="reason" />
      <TextField source="bounce_type" label="Bounce Type" />
      <TextField source="bounce_sub_type" label="Bounce Sub Type" />
      <TextField source="source_email" label="Source Email" />
      <DateField source="created_at" label="Created" showTime />
      <TextField source="sns_message_id" label="SNS Message ID" />
      <TextField source="notes" />
      <TextField source="raw_notification" label="Raw Notification" component="pre" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 400, overflow: 'auto' }} />
    </SimpleShowLayout>
  </Show>
);

export const EmailSuppressionsCreate = () => (
  <Create>
    <SimpleForm>
      <TextInput source="email" validate={[required(), email()]} fullWidth />
      <TextInput source="reason" defaultValue="manual" disabled fullWidth />
      <TextInput source="notes" fullWidth multiline helperText="Why this email was manually suppressed" />
    </SimpleForm>
  </Create>
);
