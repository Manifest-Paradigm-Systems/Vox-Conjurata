/**
 * Conjurata Orchestrator - Core Air Traffic Controller
 * Listens for player hotkeys and VTT webhooks to route requests to local/cloud AI services.
 */

const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

app.get('/health', (req, res) => {
  res.json({ status: 'active', system: 'Vox-Conjurata' });
});

// Endpoint for VTT webhooks / hotkeys
app.post('/webhook', (req, res) => {
  console.log('Received orchestration request:', req.body);
  res.status(202).send({ message: 'Request queued' });
});

app.listen(PORT, () => {
  console.log(`Conjurata Orchestrator running on port ${PORT}`);
});
