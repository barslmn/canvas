from django.db import migrations


def remove_columns(apps, schema_editor):
    # Get the correct SQL based on database engine
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            """
            ALTER TABLE canvas_chiptype
            DROP COLUMN band_path,
            DROP COLUMN bpm_path,
            DROP COLUMN csv_path,
            DROP COLUMN egt_path,
            DROP COLUMN fasta_path,
            DROP COLUMN pfb_path;
        """
        )
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            """
            CREATE TABLE canvas_chiptype_new (
                id bigint PRIMARY KEY,
                name varchar NOT NULL,
                rows integer NOT NULL,
                cols integer NOT NULL,
                bpm varchar NULL,
                csv varchar NULL,
                egt varchar NULL,
                fasta varchar NULL,
                fasta_index varchar NULL,
                pfb varchar NULL,
                band varchar NULL
            );
        """
        )

        schema_editor.execute(
            """
            INSERT INTO canvas_chiptype_new
            SELECT id, name, rows, cols, bpm, csv, egt, fasta, fasta_index, pfb, band
            FROM canvas_chiptype;
        """
        )

        schema_editor.execute("DROP TABLE canvas_chiptype;")
        schema_editor.execute(
            "ALTER TABLE canvas_chiptype_new RENAME TO canvas_chiptype;"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("canvas", "0014_cnv_user"),
    ]

    operations = [
        migrations.RunPython(remove_columns, reverse_code=migrations.RunPython.noop),
    ]
