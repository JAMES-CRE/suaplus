from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from django.contrib import admin

        from apps.accounts.admin_policy import configure_admin_policy
        configure_admin_policy(admin.site)