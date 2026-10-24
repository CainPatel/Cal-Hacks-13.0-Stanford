// Draw approval key. Owner: D.
//
// First boot: makes a P-256 keypair, keeps the private key in flash (NVS) forever.
// Every boot: prints the public key and the device IP to Serial (115200).
//
// POST /approve   body {request_id, payee, amount, job, nonce, expires_at}
//   Blinks green and shows the payment at http://<ip>:8080 until a button is pressed.
//   Approve -> 200 {"sig": hex(r||s), "nonce": ...}   sig = ECDSA-P256 over SHA-256(body bytes)
//   Reject  -> 200 {"rejected": true, "sig": ...}     sig over SHA-256("REJECT" + body bytes)
//   No press in 90 s -> 408. Already waiting on one -> 409.
// GET /pubkey     uncompressed public key, hex (04 || X || Y). Goes in config/contract.yaml.
//
// The device signs the exact bytes it received, so the executor must verify against the
// exact bytes it sent (canonical JSON). Body fields are parsed only to show them.

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include "esp_random.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/sha256.h"

const char* WIFI_SSID = "Cal Hacks 13.0";  // venue network from the spec; change for a hotspot
const char* WIFI_PASS = "hackers!";

const int APPROVE_PIN = 4, REJECT_PIN = 5, GREEN_LED = 16, RED_LED = 17;
const unsigned long WAIT_MS = 90000;

WebServer api(80);
WebServer page(8080);  // separate server so the page still loads while /approve is waiting

mbedtls_ecp_group grp;
mbedtls_mpi priv;
mbedtls_ecp_point pub;
String pubHex;

bool pending = false;
String showPayee, showAmount, showJob;
unsigned long ledOffAt = 0;

static int hwRandom(void*, unsigned char* out, size_t len) {
  esp_fill_random(out, len);  // hardware RNG, true random while WiFi is on
  return 0;
}

String toHex(const uint8_t* b, size_t n) {
  String s;
  char h[3];
  for (size_t i = 0; i < n; i++) { sprintf(h, "%02x", b[i]); s += h; }
  return s;
}

void loadOrCreateKey() {
  mbedtls_ecp_group_init(&grp);
  mbedtls_mpi_init(&priv);
  mbedtls_ecp_point_init(&pub);
  mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256R1);

  Preferences nvs;
  nvs.begin("draw", false);
  uint8_t d[32];
  if (nvs.getBytes("priv", d, 32) == 32) {
    mbedtls_mpi_read_binary(&priv, d, 32);
    mbedtls_ecp_mul(&grp, &pub, &priv, &grp.G, hwRandom, NULL);
  } else {
    mbedtls_ecp_gen_keypair(&grp, &priv, &pub, hwRandom, NULL);
    mbedtls_mpi_write_binary(&priv, d, 32);
    nvs.putBytes("priv", d, 32);
    Serial.println("Generated a new keypair.");
  }
  nvs.end();
  memset(d, 0, sizeof d);

  uint8_t q[65];
  size_t n;
  mbedtls_ecp_point_write_binary(&grp, &pub, MBEDTLS_ECP_PF_UNCOMPRESSED, &n, q, sizeof q);
  pubHex = toHex(q, n);
}

String sign(const String& msg) {
  uint8_t hash[32], sig[64];
  mbedtls_sha256((const uint8_t*)msg.c_str(), msg.length(), hash, 0);
  mbedtls_mpi r, s;
  mbedtls_mpi_init(&r);
  mbedtls_mpi_init(&s);
  mbedtls_ecdsa_sign(&grp, &r, &s, &priv, hash, sizeof hash, hwRandom, NULL);
  mbedtls_mpi_write_binary(&r, sig, 32);
  mbedtls_mpi_write_binary(&s, sig + 32, 32);
  mbedtls_mpi_free(&r);
  mbedtls_mpi_free(&s);
  return toHex(sig, sizeof sig);
}

// Raw JSON token for a top-level key (strings keep their quotes). Display only, not validation.
String field(const String& b, const char* key) {
  String k = String("\"") + key + "\"";
  int i = b.indexOf(k);
  if (i < 0) return "null";
  i = b.indexOf(':', i + k.length()) + 1;
  while (b[i] == ' ') i++;
  if (b[i] == '"') return b.substring(i, b.indexOf('"', i + 1) + 1);
  int e = i;
  while (e < (int)b.length() && b[e] != ',' && b[e] != '}') e++;
  return b.substring(i, e);
}

String plain(String token) {
  if (token.startsWith("\"")) token = token.substring(1, token.length() - 1);
  token.replace("&", "&amp;");
  token.replace("<", "&lt;");
  return token;
}

bool pressed(int pin) {
  if (digitalRead(pin) != LOW) return false;
  delay(30);  // debounce
  if (digitalRead(pin) != LOW) return false;
  unsigned long t = millis();
  while (digitalRead(pin) == LOW && millis() - t < 2000) delay(5);  // wait for release
  return true;
}

void flash(int led) {
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(RED_LED, LOW);
  digitalWrite(led, HIGH);
  ledOffAt = millis() + 3000;
}

void handleApprove() {
  if (pending) { api.send(409, "application/json", "{\"error\":\"busy\"}"); return; }
  String body = api.arg("plain");
  showPayee = plain(field(body, "payee"));
  showAmount = plain(field(body, "amount"));
  showJob = plain(field(body, "job"));
  String nonce = field(body, "nonce");
  Serial.printf("Waiting for a button: %s $%s (%s)\n", showPayee.c_str(), showAmount.c_str(), showJob.c_str());

  pending = true;
  int result = 0;
  unsigned long start = millis();
  while (millis() - start < WAIT_MS) {
    page.handleClient();
    digitalWrite(GREEN_LED, (millis() / 250) % 2);
    if (pressed(APPROVE_PIN)) { result = 1; break; }
    if (pressed(REJECT_PIN)) { result = -1; break; }
    delay(5);
  }
  pending = false;

  if (result == 1) {
    flash(GREEN_LED);
    api.send(200, "application/json", "{\"sig\":\"" + sign(body) + "\",\"nonce\":" + nonce + "}");
    Serial.println("Approved.");
  } else if (result == -1) {
    flash(RED_LED);
    api.send(200, "application/json", "{\"rejected\":true,\"sig\":\"" + sign("REJECT" + body) + "\"}");
    Serial.println("Rejected.");
  } else {
    digitalWrite(GREEN_LED, LOW);
    api.send(408, "application/json", "{\"error\":\"timeout\"}");
    Serial.println("Timed out.");
  }
}

void handlePage() {
  String html = "<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=1>"
                "<meta name=viewport content='width=device-width'>"
                "<body style='font-family:ui-monospace,Menlo,monospace;background:#faf8f5;color:#222;"
                "text-align:center;padding:12vh 16px'>";
  if (pending) {
    html += "<p style='font-size:1.4em'>Payment waiting for your OK</p>"
            "<p style='font-size:4em;font-weight:700;margin:.2em 0'>$" + showAmount + "</p>"
            "<p style='font-size:1.6em'>" + showPayee + "</p><p>" + showJob + "</p>"
            "<p style='margin-top:3em'>Press <b style='color:#1a7f37'>green</b> on the device to approve, "
            "<b style='color:#c62828'>red</b> to reject.</p>";
  } else {
    html += "<p style='font-size:1.4em;color:#888'>No payment waiting.</p>";
  }
  page.send(200, "text/html", html);
}

void setup() {
  Serial.begin(115200);
  pinMode(APPROVE_PIN, INPUT_PULLUP);
  pinMode(REJECT_PIN, INPUT_PULLUP);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  digitalWrite(RED_LED, HIGH);  // red until WiFi is up

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(250);
  digitalWrite(RED_LED, LOW);

  loadOrCreateKey();
  Serial.println("PUBKEY " + pubHex);
  Serial.println("IP " + WiFi.localIP().toString());

  api.on("/approve", HTTP_POST, handleApprove);
  api.on("/pubkey", HTTP_GET, [] { api.send(200, "text/plain", pubHex); });
  page.on("/", HTTP_GET, handlePage);
  api.begin();
  page.begin();
}

void loop() {
  api.handleClient();
  page.handleClient();
  if (ledOffAt && millis() > ledOffAt) {
    digitalWrite(GREEN_LED, LOW);
    digitalWrite(RED_LED, LOW);
    ledOffAt = 0;
  }
}
