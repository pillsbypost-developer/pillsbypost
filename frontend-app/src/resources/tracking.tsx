import { useState, useCallback } from 'react';
import {
  List,
  Datagrid,
  TextField,
  DateField,
  Create,
  Edit,
  SimpleForm,
  TextInput,
  required,
  Toolbar,
  SaveButton,
  ListButton,
  TopToolbar,
  CreateButton,
  ExportButton,
  EditButton,
  DeleteWithConfirmButton,
  useNotify,
  useRedirect,
  BooleanField,
} from 'react-admin';
import { Button, Dialog, DialogTitle, DialogContent, DialogActions, Typography, Box, Select, MenuItem, FormControl, InputLabel } from '@mui/material';
import { useDropzone } from 'react-dropzone';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import { config } from '../config';
import { authFetch } from '../dataProvider';

const NONE = '__none__';

// Auto-detect which header best matches a role
const autoDetect = (headers: string[], hints: string[]): string => {
  for (const hint of hints) {
    const match = headers.find(h => h.toLowerCase().includes(hint));
    if (match) return match;
  }
  return '';
};

// Parse a CSV line respecting quoted fields
const parseCSVLine = (line: string): string[] => {
  const result: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  result.push(current.trim());
  return result;
};

// CSV upload: the shipping vendor returns a file with the IntakeQ ClientId,
// the tracking number and (optionally) the carrier.
const CSVUploadDialog = ({ open, onClose }: { open: boolean; onClose: () => void }) => {
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[] | null>(null);
  const [rowCount, setRowCount] = useState<number>(0);
  const [clientIdCol, setClientIdCol] = useState<string>('');
  const [trackingCol, setTrackingCol] = useState<string>('');
  const [carrierCol, setCarrierCol] = useState<string>(NONE);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notify = useNotify();

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return;
    const dropped = acceptedFiles[0];
    const text = await dropped.text();
    const lines = text.split('\n').filter(line => line.trim());

    if (lines.length < 2) {
      setError('CSV must have a header row and at least one data row');
      return;
    }

    const parsedHeaders = parseCSVLine(lines[0]);
    setFile(dropped);
    setHeaders(parsedHeaders);
    setRowCount(lines.length - 1);
    setError(null);

    setClientIdCol(autoDetect(parsedHeaders, ['patient', 'recipient', 'client', 'id']));
    setTrackingCol(autoDetect(parsedHeaders, ['track']));
    setCarrierCol(autoDetect(parsedHeaders, ['carrier', 'shipper']) || NONE);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'text/csv': ['.csv'] },
    maxFiles: 1,
  });

  const handleUpload = async () => {
    if (!file || !headers || !clientIdCol || !trackingCol) return;
    setUploading(true);
    setError(null);

    try {
      const text = await file.text();
      const lines = text.split('\n').filter(line => line.trim());

      const clientIdIndex = headers.indexOf(clientIdCol);
      const trackingIndex = headers.indexOf(trackingCol);
      const carrierIndex = carrierCol === NONE ? -1 : headers.indexOf(carrierCol);

      const entries = [];
      for (let i = 1; i < lines.length; i++) {
        const values = parseCSVLine(lines[i]);
        const clientId = values[clientIdIndex] || '';
        const trackingNumber = values[trackingIndex] || '';
        const carrier = carrierIndex >= 0 ? values[carrierIndex] || '' : '';
        if (clientId || trackingNumber) {
          entries.push({
            client_id: clientId,
            tracking_number: trackingNumber,
            ...(carrier ? { carrier } : {}),
          });
        }
      }

      if (entries.length === 0) {
        setError('No valid data rows found in CSV');
        setUploading(false);
        return;
      }

      // Returns 200 for a single entry (processed inline) or 202 for a batch
      const response = await authFetch(`${config.apiUrl}/tracking-upload`, {
        method: 'POST',
        body: JSON.stringify({ entries }),
      });

      if (response.status !== 202 && response.status !== 200) {
        const body = await response.json().catch(() => ({}));
        setError(body.error || `Upload failed (HTTP ${response.status})`);
        setUploading(false);
        return;
      }

      notify(`${entries.length} entries submitted. Check Tracking Jobs in a few minutes to see results.`, { type: 'success' });
      handleClose();
    } catch {
      setError('Failed to process CSV file');
      setUploading(false);
    }
  };

  const resetFile = () => {
    setFile(null);
    setHeaders(null);
    setRowCount(0);
    setClientIdCol('');
    setTrackingCol('');
    setCarrierCol(NONE);
    setError(null);
  };

  const handleClose = () => {
    resetFile();
    setUploading(false);
    onClose();
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Upload Tracking Numbers (CSV)</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="textSecondary" gutterBottom>
          Upload the shipping vendor's CSV and pick the columns that hold the Client ID, Tracking Number and (optionally) Carrier.
        </Typography>

        {!headers ? (
          <Box
            {...getRootProps()}
            sx={{
              border: '2px dashed',
              borderColor: isDragActive ? 'primary.main' : 'grey.400',
              borderRadius: 2,
              p: 3,
              mt: 2,
              textAlign: 'center',
              cursor: 'pointer',
              bgcolor: isDragActive ? 'action.hover' : 'background.paper',
            }}
          >
            <input {...getInputProps()} />
            <UploadFileIcon sx={{ fontSize: 48, color: 'grey.500', mb: 1 }} />
            <Typography>{isDragActive ? 'Drop the CSV file here...' : 'Drag and drop a CSV file here, or click to select'}</Typography>
          </Box>
        ) : (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2" onClick={resetFile} sx={{ cursor: 'pointer', color: 'primary.main', mb: 2 }}>
              {file!.name} (click to change)
            </Typography>

            <FormControl fullWidth sx={{ mb: 2 }}>
              <InputLabel>Client ID column</InputLabel>
              <Select value={clientIdCol} label="Client ID column" onChange={e => setClientIdCol(e.target.value)}>
                {headers.map(h => <MenuItem key={h} value={h}>{h}</MenuItem>)}
              </Select>
            </FormControl>

            <FormControl fullWidth sx={{ mb: 2 }}>
              <InputLabel>Tracking number column</InputLabel>
              <Select value={trackingCol} label="Tracking number column" onChange={e => setTrackingCol(e.target.value)}>
                {headers.map(h => <MenuItem key={h} value={h}>{h}</MenuItem>)}
              </Select>
            </FormControl>

            <FormControl fullWidth sx={{ mb: 2 }}>
              <InputLabel>Carrier column (optional)</InputLabel>
              <Select value={carrierCol} label="Carrier column (optional)" onChange={e => setCarrierCol(e.target.value)}>
                <MenuItem value={NONE}><em>None</em></MenuItem>
                {headers.map(h => <MenuItem key={h} value={h}>{h}</MenuItem>)}
              </Select>
            </FormControl>

            <Typography variant="body2" color="textSecondary">
              {rowCount} {rowCount === 1 ? 'row' : 'rows'} ready to upload
            </Typography>
          </Box>
        )}

        {error && <Typography color="error" sx={{ mt: 2 }}>{error}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={uploading}>Cancel</Button>
        <Button onClick={handleUpload} disabled={!headers || !clientIdCol || !trackingCol || uploading} variant="contained">
          {uploading ? 'Uploading...' : 'Upload'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const TrackingListActions = () => {
  const [uploadOpen, setUploadOpen] = useState(false);
  return (
    <TopToolbar>
      <Button startIcon={<UploadFileIcon />} onClick={() => setUploadOpen(true)} size="small">
        Upload CSV
      </Button>
      <CreateButton />
      <ExportButton />
      <CSVUploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} />
    </TopToolbar>
  );
};

export const TrackingList = () => (
  <List actions={<TrackingListActions />} sort={{ field: 'created_at', order: 'DESC' }} empty={false}>
    <Datagrid rowClick="edit">
      <TextField source="client_id" label="Client ID" />
      <TextField source="tracking_number" label="Tracking Number" />
      <TextField source="carrier" label="Carrier" />
      <BooleanField source="synced_to_intakeq" label="Synced to IntakeQ" />
      <DateField source="created_at" label="Created" showTime />
      <EditButton />
      <DeleteWithConfirmButton />
    </Datagrid>
  </List>
);

const TrackingToolbar = () => (
  <Toolbar>
    <SaveButton />
    <ListButton />
  </Toolbar>
);

export const TrackingEdit = () => (
  <Edit>
    <SimpleForm toolbar={<TrackingToolbar />}>
      <TextInput source="tracking_id" disabled fullWidth />
      <TextInput source="client_id" label="Client ID" validate={[required()]} fullWidth />
      <TextInput source="tracking_number" label="Tracking Number" validate={[required()]} fullWidth />
      <TextInput source="carrier" label="Carrier" fullWidth />
      <TextInput source="notes" label="Notes" multiline rows={3} fullWidth />
    </SimpleForm>
  </Edit>
);

// Creating a tracking entry goes through /tracking-upload so IntakeQ and the
// order record are updated the same way as a CSV upload.
export const TrackingCreate = () => {
  const notify = useNotify();
  const redirect = useRedirect();

  const handleSave = async (data: any) => {
    try {
      const entry: Record<string, string> = {
        client_id: data.client_id,
        tracking_number: data.tracking_number,
        ...(data.carrier ? { carrier: data.carrier } : {}),
        ...(data.notes ? { notes: data.notes } : {}),
      };
      const response = await authFetch(`${config.apiUrl}/tracking-upload`, {
        method: 'POST',
        body: JSON.stringify({ entries: [entry] }),
      });

      if (response.status === 200) {
        notify('Tracking entry created successfully.', { type: 'success' });
        redirect('list', 'tracking');
      } else if (response.status === 202) {
        notify('Entry submitted for processing. It will appear in the table shortly.', { type: 'success' });
        redirect('list', 'tracking');
      } else {
        const respData = await response.json().catch(() => ({}));
        notify(respData.error || `Failed (HTTP ${response.status})`, { type: 'error' });
      }
    } catch {
      notify('Failed to submit entry', { type: 'error' });
    }
  };

  return (
    <Create>
      <SimpleForm onSubmit={handleSave} toolbar={<TrackingToolbar />}>
        <TextInput source="client_id" label="Client ID" validate={[required()]} fullWidth />
        <TextInput source="tracking_number" label="Tracking Number" validate={[required()]} fullWidth />
        <TextInput source="carrier" label="Carrier" fullWidth />
        <TextInput source="notes" label="Notes" multiline rows={3} fullWidth />
      </SimpleForm>
    </Create>
  );
};
