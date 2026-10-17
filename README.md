# AI Travel Agents

A Streamlit app that searches flights and hotels (through SerpAPI), asks a free AI
model for a short summary, and can email the results with SendGrid.

## Keys you need

| Key | Needed for | Where to get it |
|---|---|---|
| `GEMINI_API_KEY` | AI summary and airport-code lookup (free tier) | aistudio.google.com/app/apikey |
| `GROQ_API_KEY` | Optional backup AI model (free tier) | console.groq.com/keys |
| `SERPAPI_API_KEY` | Flight and hotel search | serpapi.com |
| `SENDGRID_KEY`, `SENDER_EMAIL` | Optional: sending the email | sendgrid.com (the sender must be verified) |

At least one of the two AI keys is required. Never commit real keys to GitHub.

## Run it on your PC

```bash
python -m venv venv
venv\Scripts\activate          # Windows (use: source venv/bin/activate on Mac/Linux)
python -m pip install -r requirements.txt
copy .env.example .env         # then open .env and add your keys
python -m streamlit run main.py
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. Go to share.streamlit.io, click **Create app**, pick the repository and branch,
   and set **Main file path** to `main.py`.
3. Open **Advanced settings, Secrets** (or later: **Manage app, Settings, Secrets**)
   and paste your keys in this format:

   ```toml
   GEMINI_API_KEY = "your-gemini-key"
   SERPAPI_API_KEY = "your-serpapi-key"
   ```

4. Click **Deploy**. After changing Secrets, use **Manage app, Reboot app**.

The sidebar of the running app shows which keys are set (never the keys themselves).

## Notes

- Free AI tiers have rate limits, so the app tries Gemini first and Groq second.
- The email is sent only when you choose "Yes" in the form.
- Flight search currently uses SerpAPI's `stops = 1` setting (nonstop flights only).
  Change it in `agents/flight_agent.py` if you want flights with stops.
