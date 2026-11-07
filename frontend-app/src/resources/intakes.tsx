import {
  List,
  Datagrid,
  TextField,
  NumberField,
  DateField,
  Show,
  SimpleShowLayout,
  TopToolbar,
  ExportButton,
  ShowButton,
  SelectInput,
  TextInput,
  FunctionField,
} from 'react-admin';
import { Chip } from '@mui/material';

const STATUS_CHOICES = ['QUEUED', 'PROCESSED', 'INELIGIBLE', 'SKIPPED_NON_INTAKE', 'VALIDATION_FAILED']
  .map(s => ({ id: s, name: s }));

const statusColor = (status?: string): 'success' | 'warning' | 'error' | 'default' => {
  switch (status) {
    case 'PROCESSED': return 'success';
    case 'QUEUED': return 'warning';
    case 'INELIGIBLE':
    case 'VALIDATION_FAILED': return 'error';
    default: return 'default';
  }
};

const StatusChip = ({ record }: { record?: any }) => (
  <Chip label={record?.status} color={statusColor(record?.status)} size="small" />
);

const filters = [
  <TextInput key="q" source="q" label="Search" alwaysOn />,
  <SelectInput key="status" source="status" choices={STATUS_CHOICES} />,
];

export const IntakesList = () => (
  <List actions={<TopToolbar><ExportButton /></TopToolbar>} filters={filters} sort={{ field: 'created_at', order: 'DESC' }}>
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <TextField source="intake_id" label="Intake ID" />
      <TextField source="client_id" label="Client ID" />
      <DateField source="created_at" label="Received" showTime />
      <FunctionField label="Status" render={(record: any) => <StatusChip record={record} />} />
      <TextField source="payment_tier" label="Payment Tier" />
      <NumberField source="payment_amount" label="Amount" options={{ style: 'currency', currency: 'USD' }} />
      <TextField source="state" label="State" />
      <NumberField source="age" label="Age" />
      <TextField source="workflow_path" label="Workflow" />
      <ShowButton />
    </Datagrid>
  </List>
);

export const IntakesShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="intake_id" label="Intake ID" />
      <TextField source="client_id" label="Client ID" />
      <DateField source="created_at" label="Received" showTime />
      <DateField source="processed_at" label="Processed" showTime />
      <FunctionField label="Status" render={(record: any) => <StatusChip record={record} />} />
      <TextField source="questionnaire_name" label="Questionnaire" />
      <TextField source="event_type" label="Event Type" />
      <TextField source="payment_tier" label="Payment Tier" />
      <NumberField source="payment_amount" label="Amount" options={{ style: 'currency', currency: 'USD' }} />
      <TextField source="pharmacy_choice" label="Pharmacy Choice" />
      <TextField source="state" label="State" />
      <NumberField source="age" label="Age" />
      <TextField source="eligibility_result" label="Eligibility" />
      <TextField source="email_template_sent" label="Email Template Sent" />
      <TextField source="workflow_path" label="Workflow" />
      <TextField source="validation_error" label="Validation Error" />
    </SimpleShowLayout>
  </Show>
);
