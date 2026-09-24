# Cryptogram Duel

Cryptogram Duel is a browser-based cryptogram game with a Flask backend. A player registers with a username, loads another registered player's public key, sends a substitution-cipher puzzle, and earns a point when submitting the correct answer. The page also shows a shared scoreboard.

This repository currently consists of `kms.py` (Flask API) and `chat.html` (single-page frontend). The frontend expects to be served by Flask from `static/chat.html`.

## Features

- Register usernames through a CFSSL signing service.
- Look up a registered user's certificate and public key.
- Create a random monoalphabetic substitution puzzle in the browser.
- Encrypt puzzle text with AES-GCM; wrap the data-encryption key (DEK) with Google Cloud KMS before storing the message.
- Poll a user's inbox and display received puzzles.
- Track points and show a ranked scoreboard.

## How it works

1. The browser creates an RSA-OAEP key pair and sends the public key PEM with the username to `POST /api/register`.
2. The backend submits a certificate-signing request to CFSSL and stores the returned certificate in memory.
3. The sender chooses a peer, creates a random letter-substitution mapping, transforms the phrase, and encrypts the transformed text with AES-GCM.
4. The browser sends the ciphertext, IV, substitution-map metadata, and base64-encoded DEK to the backend. The backend encrypts (wraps) the DEK with Google Cloud KMS and queues the message for the recipient.
5. The recipient polls the inbox. The browser asks the backend to unwrap the DEK, decrypts the ciphertext, and renders the puzzle.
6. A correct guess calls the score endpoint, which increments the solver's score.

## Requirements

- Python 3.10 or newer
- Flask
- `requests`
- `cryptography`
- `google-cloud-kms`
- A reachable CFSSL signing service with the `client` profile
- A Google Cloud project with the configured Cloud KMS key ring and key, plus application credentials authorized to encrypt and decrypt with that key
- A secure origin (HTTPS) for browser Web Crypto APIs and for protecting requests in transit

Install the Python packages:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install Flask requests cryptography google-cloud-kms
```

## Project layout

Place the files in this layout so Flask can serve the page at `/`:

```text
.
├── kms.py
└── static/
    └── chat.html
```

The provided `chat.html` should be moved into the `static/` directory. `kms.py` configures Flask with `static_folder="static"` and serves `chat.html` from that folder.

## Configuration

The backend reads these environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CFSSL_URL` | `http://localhost:8888` | Base URL of the CFSSL service. Registration calls `/api/v1/cfssl/sign`. |
| `GOOGLE_CLOUD_PROJECT` | `winter-ace-353107` | Google Cloud project containing the KMS key. Set this to your own project. |
| `PORT` | `8080` | Flask listening port. |
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | Optional path to a Google Cloud service-account credentials JSON file. Alternatively, configure Application Default Credentials in the runtime environment. |

The KMS location (`global`), key ring (`chat-keyring`), key (`chat-kek`), and CFSSL profile (`client`) are currently hard-coded in `kms.py`. Create matching KMS resources or update those constants before running the service.

Example:

```bash
export CFSSL_URL="http://localhost:8888"
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
export GOOGLE_APPLICATION_CREDENTIALS="$PWD/service-account.json"
export PORT=8080
python kms.py
```

Open `http://localhost:8080/` for local testing. In production, put the app behind HTTPS and configure the CFSSL and Google Cloud credentials for the server environment. Do not commit service-account credentials to Git.

## API

All request and response bodies are JSON unless noted otherwise.

| Method and path | Purpose |
| --- | --- |
| `GET /` | Serves `static/chat.html`. |
| `POST /api/register` | Registers a username and submits its supplied public key PEM for signing. Body: `{"username":"...","publicKeyPem":"..."}`. Returns a certificate. |
| `GET /api/cert/<username>` | Returns the user's certificate and public key PEM. |
| `POST /api/message` | Queues a puzzle for a registered recipient. Required fields: `from`, `to`, `ciphertext`, `iv`, `dek`, `metadata`. The backend returns a message ID after wrapping the DEK with KMS. |
| `POST /api/unwrap` | Unwraps a KMS-wrapped DEK. Body: `{"wrappedKey":"<base64>"}`. |
| `GET /api/messages?for=<username>` | Retrieves and removes the user's queued messages from the in-memory inbox. |
| `POST /api/score` | Increments a registered solver's score. Body: `{"solver":"..."}`. |
| `GET /api/scoreboard` | Returns users and scores in descending score order. |
