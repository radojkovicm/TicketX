# Security policy

## Supported version

Security updates are applied to the latest commit on the `main` branch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not include credentials, personal data, production databases or real attachments in a report.

Include:

- the affected route or component;
- the impact and required user role;
- minimal reproduction steps;
- a proposed fix, if available.

You should receive an acknowledgement within seven days. Public disclosure should wait until a fix is available.

## Deployment responsibility

TicketX must be deployed behind HTTPS with a unique `FLASK_SECRET_KEY`. Operators are responsible for access control to `.env`, the database, backups and the upload directory.
