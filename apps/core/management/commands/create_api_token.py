"""Issue and revoke automation API tokens.

    python manage.py create_api_token --name ci --scope read --scope deploy

The raw token is printed once. Store it in the CI secret store, not in Git.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import APIToken


class Command(BaseCommand):
    help = "Create, list or revoke scoped API tokens."

    def add_arguments(self, parser):
        parser.add_argument("--name", help="Human-readable token name.")
        parser.add_argument(
            "--scope",
            action="append",
            choices=[choice.value for choice in APIToken.Scope],
            help="Repeatable. Defaults to 'read'.",
        )
        parser.add_argument("--list", action="store_true", help="List tokens.")
        parser.add_argument("--revoke", metavar="PREFIX", help="Disable a token by prefix.")

    def handle(self, *args, **options):
        if options["list"]:
            for token in APIToken.objects.all():
                state = "active" if token.active else "disabled"
                self.stdout.write(
                    f"{token.prefix}  {token.name:<24} {state:<8} "
                    f"{','.join(token.scopes)}  last_used={token.last_used_at or 'never'}"
                )
            return

        if options["revoke"]:
            updated = APIToken.objects.filter(prefix=options["revoke"]).update(active=False)
            if not updated:
                raise CommandError(f"No token with prefix {options['revoke']!r}.")
            self.stdout.write(self.style.SUCCESS(f"Revoked {updated} token(s)."))
            return

        if not options["name"]:
            raise CommandError("--name is required (or use --list/--revoke).")
        token, raw = APIToken.issue(options["name"], options["scope"] or ["read"])
        self.stdout.write(self.style.SUCCESS("API token created. It will not be shown again."))
        self.stdout.write(f"name:   {token.name}")
        self.stdout.write(f"scopes: {', '.join(token.scopes)}")
        self.stdout.write(f"token:  {raw}")
