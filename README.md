# nammareply
NammaReply - AI receptionist on WhatsApp and email for Bengaluru businesses.

- `index.html` - landing page (GitHub Pages)
- `assistant/` - assistant core (Python 3, standard library only, runs on Termux)
- `tests/` - `python3 -m unittest discover -s tests -t .`

## Try the assistant locally (nothing is sent anywhere)
    python3 -m assistant.simulate

Per-client settings (services, prices, hours, FAQs, approval topics) live in `assistant/clients/*.json`.
Money, complaint and unknown questions go to the owner as `YES <id>` / `NO <id>` approvals.
