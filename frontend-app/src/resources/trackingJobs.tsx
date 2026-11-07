import {
  List,
  Datagrid,
  TextField,
  DateField,
  Show,
  SimpleShowLayout,
  ArrayField,
  ChipField,
  TopToolbar,
  ShowButton,
  FunctionField,
} from 'react-admin';
import { Chip } from '@mui/material';

const StatusChip = ({ record }: { record?: any }) => {
  const status = record?.status;
  const color = status === 'COMPLETED' ? 'success' : status === 'PROCESSING' ? 'warning' : status === 'FAILED' ? 'error' : 'default';
  return <Chip label={status} color={color} size="small" />;
};

export const TrackingJobsList = () => (
  <List actions={<TopToolbar />} sort={{ field: 'created_at', order: 'DESC' }}>
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <DateField source="created_at" label="Submitted" showTime />
      <FunctionField label="Status" render={(record: any) => <StatusChip record={record} />} />
      <TextField source="total" label="Total" />
      <TextField source="succeeded" label="Succeeded" />
      <TextField source="failed" label="Failed" />
      <DateField source="completed_at" label="Completed" showTime />
      <ShowButton />
    </Datagrid>
  </List>
);

export const TrackingJobsShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="job_id" label="Job ID" />
      <FunctionField label="Status" render={(record: any) => <StatusChip record={record} />} />
      <TextField source="total" label="Total Entries" />
      <TextField source="succeeded" label="Succeeded" />
      <TextField source="failed" label="Failed" />
      <DateField source="created_at" label="Submitted" showTime />
      <DateField source="completed_at" label="Completed" showTime />
      <ArrayField source="results" label="Results">
        <Datagrid bulkActionButtons={false}>
          <TextField source="client_id" label="Client ID" />
          <TextField source="tracking_number" label="Tracking Number" />
          <ChipField source="status" label="Status" />
          <TextField source="error" label="Error" />
        </Datagrid>
      </ArrayField>
    </SimpleShowLayout>
  </Show>
);
