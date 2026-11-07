import { useState } from 'react';
import {
  List,
  Datagrid,
  TextField,
  NumberField,
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
  useListContext,
  useNotify,
  useRefresh,
  useRecordContext,
  useUnselectAll,
  Button as RaButton,
} from 'react-admin';
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField as MuiTextField,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PaidIcon from '@mui/icons-material/Paid';
import CancelIcon from '@mui/icons-material/Cancel';
import ReplayIcon from '@mui/icons-material/Replay';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import {
  exportOrdersBatch,
  markOrderPaid,
  cancelOrder,
  resubmitOrderToNectar,
  markOrderShipped,
} from '../dataProvider';

// ---------------------------------------------------------------------------
// Constants and small helpers
// ---------------------------------------------------------------------------

const STATUSES = [
  'NEW', 'EXPORTED', 'SUBMITTED_TO_NECTAR', 'NECTAR_ON_HOLD',
  'SHIPPED', 'DELIVERED', 'LOCAL_PHARMACY', 'CANCELED',
];
const STATUS_CHOICES = STATUSES.map(s => ({ id: s, name: s }));

const FULFILLMENT_CHOICES = [
  { id: 'shipping', name: 'shipping' },
  { id: 'nectar', name: 'nectar' },
  { id: 'local_pharmacy', name: 'local_pharmacy' },
];

const TERMINAL_STATUSES = ['DELIVERED', 'CANCELED'];

const statusColor = (status?: string): 'success' | 'warning' | 'error' | 'info' | 'default' => {
  switch (status) {
    case 'SHIPPED':
    case 'DELIVERED': return 'success';
    case 'NEW':
    case 'EXPORTED':
    case 'SUBMITTED_TO_NECTAR': return 'info';
    case 'NECTAR_ON_HOLD': return 'warning';
    case 'CANCELED': return 'error';
    default: return 'default';
  }
};

const StatusChip = ({ record }: { record?: any }) => (
  <Chip label={record?.status} color={statusColor(record?.status)} size="small" />
);

const downloadCSV = (csv: string, filename: string) => {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};

const errorMessage = (error: unknown) =>
  (error as any)?.body?.message || (error as any)?.message || String(error);

// ---------------------------------------------------------------------------
// Bulk action: Export selected (shipping orders)
// ---------------------------------------------------------------------------

const ExportSelectedButton = () => {
  const { selectedIds } = useListContext();
  const notify = useNotify();
  const refresh = useRefresh();
  const unselectAll = useUnselectAll('orders');
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    if (selectedIds.length === 0) return;
    setExporting(true);
    try {
      const result = await exportOrdersBatch(selectedIds.map(String));
      const timestamp = new Date().toISOString().slice(0, 10);
      downloadCSV(result.csv, `orders-export-${timestamp}.csv`);
      notify(`Exported ${result.exported_count} order(s)`, { type: 'success' });
      unselectAll();
      refresh();
    } catch (error) {
      notify(`Export failed: ${errorMessage(error)}`, { type: 'error' });
    } finally {
      setExporting(false);
    }
  };

  return (
    <RaButton label={exporting ? 'Exporting...' : 'Export selected'} onClick={handleExport} disabled={exporting}>
      <DownloadIcon />
    </RaButton>
  );
};

// ---------------------------------------------------------------------------
// Row / show actions
// ---------------------------------------------------------------------------

type ActionDialog = 'mark-paid' | 'cancel' | 'mark-shipped' | null;

const OrderActions = () => {
  const record = useRecordContext();
  const notify = useNotify();
  const refresh = useRefresh();
  const [dialog, setDialog] = useState<ActionDialog>(null);
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [trackingNumber, setTrackingNumber] = useState('');
  const [carrier, setCarrier] = useState('');
  const [busy, setBusy] = useState(false);

  if (!record) return null;
  const orderId = String(record.order_id);
  const terminal = TERMINAL_STATUSES.includes(record.status);

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      notify(`${label} succeeded`, { type: 'success' });
      setDialog(null);
      refresh();
    } catch (error) {
      notify(`${label} failed: ${errorMessage(error)}`, { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const openDialog = (which: ActionDialog) => {
    setAmount(record.payment_amount != null ? String(record.payment_amount) : '');
    setReason('');
    setTrackingNumber(record.tracking_number || '');
    setCarrier(record.carrier || '');
    setDialog(which);
  };

  return (
    <Stack direction="row" spacing={0.5} onClick={e => e.stopPropagation()}>
      <Button size="small" startIcon={<PaidIcon />} onClick={() => openDialog('mark-paid')} disabled={terminal}>
        Mark paid
      </Button>
      <Button size="small" startIcon={<LocalShippingIcon />} onClick={() => openDialog('mark-shipped')} disabled={terminal}>
        Mark shipped
      </Button>
      {record.fulfillment_mode === 'nectar' && (
        <Button
          size="small"
          startIcon={<ReplayIcon />}
          disabled={busy || terminal}
          onClick={() => run('Resubmit to Nectar', () => resubmitOrderToNectar(orderId))}
        >
          Resubmit to Nectar
        </Button>
      )}
      <Button size="small" color="error" startIcon={<CancelIcon />} onClick={() => openDialog('cancel')} disabled={terminal}>
        Cancel
      </Button>

      <Dialog open={dialog === 'mark-paid'} onClose={() => setDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Mark order {orderId} as paid</DialogTitle>
        <DialogContent>
          <MuiTextField
            autoFocus
            margin="dense"
            label="Amount paid (USD)"
            type="number"
            fullWidth
            value={amount}
            onChange={e => setAmount(e.target.value)}
            slotProps={{ htmlInput: { min: 0, step: '0.01' } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)} disabled={busy}>Close</Button>
          <Button
            variant="contained"
            disabled={busy || amount === '' || Number(amount) < 0}
            onClick={() => run('Mark paid', () => markOrderPaid(orderId, Number(amount)))}
          >
            Mark paid
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'mark-shipped'} onClose={() => setDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Mark order {orderId} as shipped</DialogTitle>
        <DialogContent>
          <MuiTextField
            autoFocus
            margin="dense"
            label="Tracking number"
            fullWidth
            value={trackingNumber}
            onChange={e => setTrackingNumber(e.target.value)}
          />
          <MuiTextField
            margin="dense"
            label="Carrier (optional)"
            fullWidth
            value={carrier}
            onChange={e => setCarrier(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)} disabled={busy}>Close</Button>
          <Button
            variant="contained"
            disabled={busy || !trackingNumber.trim()}
            onClick={() => run('Mark shipped', () => markOrderShipped(orderId, trackingNumber.trim(), carrier.trim() || undefined))}
          >
            Mark shipped
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={dialog === 'cancel'} onClose={() => setDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Cancel order {orderId}?</DialogTitle>
        <DialogContent>
          <MuiTextField
            autoFocus
            margin="dense"
            label="Reason (optional)"
            fullWidth
            multiline
            value={reason}
            onChange={e => setReason(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)} disabled={busy}>Keep order</Button>
          <Button
            variant="contained"
            color="error"
            disabled={busy}
            onClick={() => run('Cancel order', () => cancelOrder(orderId, reason.trim() || undefined))}
          >
            Cancel order
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

const filters = [
  <TextInput key="q" source="q" label="Search" alwaysOn />,
  <SelectInput key="status" source="status" choices={STATUS_CHOICES} alwaysOn />,
  <SelectInput key="fulfillment_mode" source="fulfillment_mode" label="Fulfillment" choices={FULFILLMENT_CHOICES} alwaysOn />,
];

export const OrdersList = () => (
  <List
    actions={<TopToolbar><ExportButton /></TopToolbar>}
    filters={filters}
    filterDefaultValues={{ status: 'NEW' }}
    sort={{ field: 'approval_date', order: 'DESC' }}
  >
    <Datagrid rowClick="show" bulkActionButtons={<ExportSelectedButton />}>
      <TextField source="order_id" label="Order ID" />
      <TextField source="client_id" label="Client ID" />
      <TextField source="client_name" label="Client" />
      <DateField source="approval_date" label="Approved" />
      <TextField source="fulfillment_mode" label="Fulfillment" />
      <FunctionField label="Status" render={(record: any) => <StatusChip record={record} />} />
      <TextField source="payment_tier" label="Tier" />
      <NumberField source="payment_amount" label="Amount" options={{ style: 'currency', currency: 'USD' }} />
      <TextField source="tracking_number" label="Tracking" />
      <TextField source="honeybee_order_number" label="Honeybee Order" />
      <OrderActions />
      <ShowButton />
    </Datagrid>
  </List>
);

// ---------------------------------------------------------------------------
// Show
// ---------------------------------------------------------------------------

export const OrdersShow = () => (
  <Show actions={<TopToolbar><OrderActions /></TopToolbar>}>
    <SimpleShowLayout>
      <TextField source="order_id" label="Order ID (IntakeQ appointment)" />
      <FunctionField label="Status" render={(record: any) => <StatusChip record={record} />} />
      <TextField source="fulfillment_mode" label="Fulfillment mode" />
      <DateField source="approval_date" label="Approval date" />
      <TextField source="appointment_type_name" label="Appointment type" />
      <TextField source="appointment_type_id" label="Appointment type ID" />

      <TextField source="client_id" label="Client ID" />
      <TextField source="client_name" label="Client name" />
      <TextField source="email" label="Email" />
      <TextField source="dob" label="DOB" />

      <TextField source="shipping_address" label="Shipping address" />
      <TextField source="unit_number" label="Unit number" />
      <TextField source="care_of" label="Care of" />
      <TextField source="city" label="City" />
      <TextField source="state" label="State" />
      <TextField source="postal_code" label="Postal code" />
      <TextField source="country" label="Country" />

      <TextField source="payment_tier" label="Payment tier" />
      <NumberField source="payment_amount" label="Payment amount" options={{ style: 'currency', currency: 'USD' }} />

      <TextField source="tracking_number" label="Tracking number" />
      <TextField source="carrier" label="Carrier" />
      <BooleanField source="tracking_synced_to_intakeq" label="Tracking synced to IntakeQ" />
      <DateField source="exported_at" label="Exported at" showTime />
      <TextField source="export_batch_id" label="Export batch" />
      <DateField source="shipped_at" label="Shipped at" showTime />

      <TextField source="honeybee_patient_id" label="Honeybee patient ID" />
      <FunctionField
        label="Honeybee prescription IDs"
        render={(record: any) => (record?.honeybee_prescription_ids || []).join(', ')}
      />
      <TextField source="honeybee_order_number" label="Honeybee order number" />
      <TextField source="honeybee_order_status" label="Honeybee order status" />
      <TextField source="honeybee_exception" label="Honeybee exception" />

      <DateField source="created_at" label="Created" showTime />
      <DateField source="updated_at" label="Updated" showTime />
    </SimpleShowLayout>
  </Show>
);
