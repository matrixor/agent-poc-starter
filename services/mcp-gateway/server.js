// Placeholder MCP gateway/registry container
// Replace with your real implementation.
// This just opens a TCP port and echoes JSON lines.

const net = require('net');

const PORT = 6000;
const server = net.createServer((socket) => {
  console.log('Client connected');
  socket.on('data', (data) => {
    const line = data.toString().trim();
    console.log('> ', line);
    // naive echo
    socket.write(JSON.stringify({ ok: true, echo: line }) + "\n");
  });
  socket.on('end', () => console.log('Client disconnected'));
});
server.listen(PORT, () => console.log(`MCP placeholder listening on :${PORT}`));
