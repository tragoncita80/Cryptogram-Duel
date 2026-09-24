from flask import Flask, request, jsonify
import os, base64, requests, uuid
from google.cloud import kms
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

app = Flask(__name__, static_folder="static")

# Config
CFSSL_URL     = os.getenv("CFSSL_URL", "http://localhost:8888")
CFSSL_PROFILE = "client"
PROJECT_ID    = os.getenv("GOOGLE_CLOUD_PROJECT", "winter-ace-353107")
KMS_LOCATION  = "global"
KMS_KEYRING   = "chat-keyring"
KMS_KEY       = "chat-kek"

# KMS client
kms_client   = kms.KeyManagementServiceClient()
kms_key_name = kms_client.crypto_key_path(
    PROJECT_ID, KMS_LOCATION, KMS_KEYRING, KMS_KEY
)

# In-memory stores
users     = {}      # username -> cert PEM
messages  = {}      # username -> list of {id,from,ciphertext,iv,wrappedKey,metadata}
scores    = {}      # username -> int

def generate_csr(username: str, public_key_pem: str) -> bytes:
    pub = serialization.load_pem_public_key(public_key_pem.encode())
    csr = x509.CertificateSigningRequestBuilder() \
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, username)])) \
        .sign(rsa.generate_private_key(65537,2048), hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM)

@app.route("/")
def index():
    return app.send_static_file("chat.html")

@app.route("/api/register", methods=["POST"])
def register():
    d = request.get_json()
    u, pk = d.get("username"), d.get("publicKeyPem")
    if not u or not pk:
        return jsonify(error="username & publicKeyPem required"), 400

    csr = generate_csr(u, pk)
    try:
        r = requests.post(f"{CFSSL_URL}/api/v1/cfssl/sign",
            json={"certificate_request": csr.decode(), "profile": CFSSL_PROFILE})
        r.raise_for_status(); j = r.json()
        if not j.get("success"):
            return jsonify(error="CSR signing failed", details=j.get("errors")), 500
        cert_pem = j["result"]["certificate"]
    except Exception as e:
        return jsonify(error="CFSSL error", details=str(e)), 500

    users[u] = cert_pem
    scores.setdefault(u, 0)
    return jsonify(certificate=cert_pem)

@app.route("/api/cert/<username>")
def get_cert(username):
    cert = users.get(username)
    if not cert:
        return jsonify(error="User not found"), 404
    x509cert = x509.load_pem_x509_certificate(cert.encode())
    spki = x509cert.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return jsonify(certificate=cert, publicKeyPem=spki)

@app.route("/api/message", methods=["POST"])
def post_message():
    d = request.get_json()
    for f in ("from","to","ciphertext","iv","dek","metadata"):
        if f not in d:
            return jsonify(error=f"{f} required"), 400
    sender, recipient = d["from"], d["to"]
    if recipient not in users:
        return jsonify(error="Recipient not found"), 400

    dek_bytes = base64.b64decode(d["dek"])
    try:
        enc = kms_client.encrypt(request={"name": kms_key_name, "plaintext": dek_bytes})
        wrapped = base64.b64encode(enc.ciphertext).decode()
    except Exception as e:
        return jsonify(error="KMS encrypt failed", details=str(e)), 500

    msg = {
        "id":         str(uuid.uuid4()),
        "from":       sender,
        "ciphertext": d["ciphertext"],
        "iv":         d["iv"],
        "wrappedKey": wrapped,
        "metadata":   d["metadata"]
    }
    messages.setdefault(recipient, []).append(msg)
    return jsonify(status="stored", id=msg["id"])

@app.route("/api/unwrap", methods=["POST"])
def unwrap_key():
    wk = request.get_json().get("wrappedKey")
    if not wk:
        return jsonify(error="wrappedKey required"), 400
    try:
        dec = kms_client.decrypt(request={"name": kms_key_name, "ciphertext": base64.b64decode(wk)})
        return jsonify(dek=base64.b64encode(dec.plaintext).decode())
    except Exception as e:
        return jsonify(error="KMS decrypt failed", details=str(e)), 500

@app.route("/api/messages", methods=["GET"])
def get_messages():
    u = request.args.get("for")
    if not u:
        return jsonify(error="Specify user via ?for=username"), 400
    inbox = messages.pop(u, [])
    return jsonify(messages=inbox)

@app.route("/api/score", methods=["POST"])
def increment_score():
    solver = request.get_json().get("solver")
    if not solver or solver not in users:
        return jsonify(error="Valid solver required"), 400
    scores[solver] = scores.get(solver, 0) + 1
    return jsonify(status="ok", score=scores[solver])

@app.route("/api/scoreboard")
def scoreboard():
    sb = [{"user":u,"score":s} for u,s in scores.items()]
    sb.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(scoreboard=sb)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",8080)))