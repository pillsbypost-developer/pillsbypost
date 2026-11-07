import {
  List,
  Datagrid,
  TextField,
  DateField,
  BooleanField,
  Show,
  SimpleShowLayout,
  TopToolbar,
  ExportButton,
  ShowButton,
  SelectInput,
  TextInput,
  FunctionField,
} from 'react-admin';

const EVENT_TYPES = ['RX_RECEIVED', 'RX_UPDATED', 'SHIPMENT_UPDATED', 'ORDER_EXCEPTION']
  .map(s => ({ id: s, name: s }));

const filters = [
  <TextInput key="q" source="q" label="Search" alwaysOn />,
  <SelectInput key="event_type" source="event_type" choices={EVENT_TYPES} />,
];

const RawField = () => (
  <FunctionField
    label="Raw Event"
    render={(record: any) => (
      <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 400, overflow: 'auto' }}>
        {typeof record?.raw === 'string' ? record.raw : JSON.stringify(record?.raw ?? {}, null, 2)}
      </pre>
    )}
  />
);

export const HoneybeeEventsList = () => (
  <List actions={<TopToolbar><ExportButton /></TopToolbar>} filters={filters} sort={{ field: 'received_at', order: 'DESC' }}>
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <TextField source="event_id" label="Event ID" />
      <TextField source="event_type" label="Event Type" />
      <TextField source="patient_id" label="Honeybee Patient ID" />
      <TextField source="order_id" label="Order ID" />
      <DateField source="received_at" label="Received" showTime />
      <BooleanField source="processed" label="Processed" />
      <ShowButton />
    </Datagrid>
  </List>
);

export const HoneybeeEventsShow = () => (
  <Show>
    <SimpleShowLayout>
      <TextField source="event_id" label="Event ID" />
      <TextField source="event_type" label="Event Type" />
      <TextField source="patient_id" label="Honeybee Patient ID" />
      <TextField source="order_id" label="Order ID" />
      <TextField source="honeybee_order_number" label="Honeybee Order Number" />
      <DateField source="received_at" label="Received" showTime />
      <BooleanField source="processed" label="Processed" />
      <TextField source="processing_error" label="Processing Error" />
      <RawField />
    </SimpleShowLayout>
  </Show>
);
