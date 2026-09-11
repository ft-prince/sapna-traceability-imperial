"""
Machine tables written by Node-RED into Postgres schema `trace`.

Generated with inspectdb and cleaned. Every model is managed=False: Django only
reads these tables and never migrates, renames or alters them. Column names are
kept exactly as in the database (including the `pervious_status` typo).
"""
from django.db import models


class _Prep(models.Model):
    """Common shape of *_preprocessing tables (one row per part loaded)."""
    id = models.AutoField(primary_key=True)
    timestamp = models.CharField(max_length=100)
    machine_name = models.CharField(max_length=50)
    qr_data = models.CharField(max_length=100)
    pervious_status = models.CharField(max_length=20)

    class Meta:
        abstract = True
        ordering = ['-id']

    def __str__(self):
        return f'{self.id} {self.qr_data} @ {self.timestamp}'


class _Post(models.Model):
    """Common shape of *_postprocessing tables (one row per part judged)."""
    id = models.AutoField(primary_key=True)
    timestamp = models.CharField(max_length=100)
    qr_data = models.CharField(max_length=100)
    status = models.CharField(max_length=20)
    pre_id = models.IntegerField(blank=True, null=True)
    # Written only by the Recheck page, never by Node-RED. Added to the plant
    # schema by hand (see README); the models stay managed = False.
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        abstract = True
        ordering = ['-id']

    def __str__(self):
        return f'{self.id} {self.qr_data} {self.status}'


class CiPreprocessing(_Prep):
    class Meta(_Prep.Meta):
        managed = False
        db_table = 'ci_preprocessing'


class CiPostprocessing(_Post):
    class Meta(_Post.Meta):
        managed = False
        db_table = 'ci_postprocessing'


class AutoPreprocessing(_Prep):
    batch_id = models.CharField(max_length=50, blank=True, null=True)
    batch_size = models.IntegerField(blank=True, null=True)
    slot_no = models.IntegerField(blank=True, null=True)

    class Meta(_Prep.Meta):
        managed = False
        db_table = 'auto_preprocessing'


class AutoPostprocessing(_Post):
    batch_id = models.CharField(max_length=50, blank=True, null=True)

    class Meta(_Post.Meta):
        managed = False
        db_table = 'auto_postprocessing'


class HeliumPreprocessing(_Prep):
    class Meta(_Prep.Meta):
        managed = False
        db_table = 'helium_preprocessing'


class HeliumPostprocessing(_Post):
    class Meta(_Post.Meta):
        managed = False
        db_table = 'helium_postprocessing'


class OperatorAssignment(models.Model):
    """
    Restricts an operator to a subset of stations on the Recheck page.

    `machines` holds ids from permissions.get_all_machines(). Machines are
    config dicts in views.py, not database rows, so ids are stored as plain
    strings rather than as a ForeignKey.

    No row for a user means unrestricted, so existing logins keep working.
    A row with an empty list locks the operator out of every station.
    """
    user = models.OneToOneField(
        'auth.User', on_delete=models.CASCADE, related_name='operator_assignment',
    )
    machines = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True          # the one app-owned table; everything else is Node-RED's
        db_table = 'operator_assignment'
        ordering = ['user__username']
        verbose_name = 'Access Control - Operator Station'
        verbose_name_plural = 'Access Control - Operator Stations'

    def __str__(self):
        return f'{self.user.username}: {len(self.machines or [])} stations'
