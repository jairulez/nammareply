# nammareply
NammaReply - AI receptionist on WhatsApp and email for Bengaluru businesses.

- `index.html` - landing page (GitHub Pages)
- `assistant/` - assistant core (Python 3, standard library only, runs on Termux)
- `tests/` - `python3 -m unittest discover -s tests -t .`

## Try the assistant locally (nothing is sent anywhere)
    python3 -m assistant.simulate

Per-client settings (services, prices, hours, FAQs, approval topics) live in `assistant/clients/*.json`.
Money, complaint and unknown questions go to the owner as `YES <id>` / `NO <id>` approvals.

## WhatsApp (Stage 3)
    cp whatsapp.env.example whatsapp.env      # fill ids; token and app secret go in separate files
    python3 -m assistant.whatsapp             # starts in DRY-RUN: prints replies, sends nothing
Set `WA_DRY_RUN=0` only after the Meta test number is set up. The webhook needs a public HTTPS URL (a free tunnel to Termux).
Only numbers in `WA_ALLOWED_NUMBERS` get replies. Never put the token in this repo.
