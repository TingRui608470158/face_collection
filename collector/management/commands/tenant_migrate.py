from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command

from collector.models import DatabaseConfig
from collector.tenant import ensure_database_connection


class Command(BaseCommand):
    help = "Run migrations for dynamic tenant databases defined in DatabaseConfig."

    def add_arguments(self, parser):  # type: ignore[override]
        parser.add_argument('--alias', help='Database alias to migrate')
        parser.add_argument('--all', action='store_true', help='Migrate all aliases in DatabaseConfig')
        parser.add_argument('--plan', action='store_true', help='Show migration plan only')

    def handle(self, *args, **options):  # type: ignore[override]
        migrate_all = options.get('all')
        alias = options.get('alias')
        plan = options.get('plan')

        if not migrate_all and not alias:
            raise CommandError('Provide --alias <alias> or --all')

        aliases = []
        if migrate_all:
            aliases = list(DatabaseConfig.objects.values_list('alias', flat=True))
            if not aliases:
                self.stdout.write(self.style.WARNING('No DatabaseConfig found.'))
                return
        else:
            aliases = [alias]

        for a in aliases:
            self.stdout.write(self.style.NOTICE(f'Ensuring connection for {a}'))
            ensure_database_connection(a)
            self.stdout.write(self.style.WARNING(f'Running migrate for {a}...'))
            call_opts = {'database': a}
            if plan:
                call_opts['plan'] = True
            call_command('migrate', **call_opts)
            self.stdout.write(self.style.SUCCESS(f'Done {a}'))


