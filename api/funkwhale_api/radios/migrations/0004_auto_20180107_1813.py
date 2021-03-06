# Generated by Django 2.0 on 2018-01-07 18:13

from django.conf import settings
import django.contrib.postgres.fields.jsonb
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("radios", "0003_auto_20160521_1708"),
    ]

    operations = [
        migrations.CreateModel(
            name="Radio",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                (
                    "creation_date",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("is_public", models.BooleanField(default=False)),
                ("version", models.PositiveIntegerField(default=0)),
                ("config", django.contrib.postgres.fields.jsonb.JSONField()),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="radios",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="radiosession",
            name="custom_radio",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sessions",
                to="radios.Radio",
            ),
        ),
    ]
