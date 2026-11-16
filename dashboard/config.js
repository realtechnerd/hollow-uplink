// Local dev: dev/broker.py or docker compose expose WebSockets on 9001.
// HiveMQ Cloud: "wss://<your-cluster>.s1.eu.hivemq.cloud:8884/mqtt" plus a dashboard-only user.
// Anything here is visible to whoever opens the page, so never reuse the gateways' credentials.
window.HOLLOW_CONFIG = {
  brokerUrl: "ws://localhost:9001",
  username: "",
  password: "",
  trustedKeysUrl: "../keys/trusted.json",
};
