import json
import secrets
import socket
import struct
import subprocess
import tempfile

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django_htmx.http import retarget
from django.http import HttpResponse
import csv
from django.http import FileResponse, HttpResponseForbidden, Http404, HttpResponse
from django.shortcuts import get_object_or_404
import zipfile
from io import BytesIO
import os
from wsgiref.util import FileWrapper
from minio import Minio

from canvas.models import (
    IDAT,
    Chip,
    ChipSample,
    ChipType,
    Institution,
    Sample,
    SampleType,
    Report,
    CNV,
    Classification,
)

from canvas.read_tsv import read_sample_from_tsv


acmg_loss = {
    "type": "loss",
    "sections": [
        {
            "id": "section1",
            "name": "Section 1: Initial Assessment of Genomic Content",
            "evidences": {
                "group1": [
                    {
                        "id": "e1A",
                        "name": "1A",
                        "description": "Contains protein-coding or other known functionally important elements",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 1,
                    },
                    {
                        "id": "e1B",
                        "name": "1B",
                        "description": "Does NOT contain protein-coding or any known functionally important elements",
                        "score": 0,
                        "suggested": -0.60,
                        "min": -0.60,
                        "max": -0.60,
                    },
                ],
            },
        },
        {
            "id": "section2",
            "name": "Section 2: Overlap with Established/Predicted HI or Established Benign Genes/Genomic Regions",
            "evidences": {
                "group1": [
                    {
                        "id": "e2A",
                        "name": "2A",
                        "description": "Complete overlap of an established HI gene/genomic region",
                        "score": 0,
                        "suggested": 1.00,
                        "min": 0.00,
                        "max": 1.00,
                        "slider": True,
                    },
                    {
                        "id": "e2B",
                        "name": "2B",
                        "description": "Partial overlap of an established HI genomic region",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                ],
                "group2": [
                    {
                        "id": "e2C1",
                        "name": "2C-1",
                        "description": "Partial overlap with the 5’ end of an established HI gene, and coding sequence is involved",
                        "score": 0,
                        "suggested": 0.90,
                        "min": 0.45,
                        "max": 1.00,
                        "slider": True,
                    },
                    {
                        "id": "e2C2",
                        "name": "2C-2",
                        "description": "Partial overlap with the 5’ end of an established HI gene, and only the 5’ UTR is involved",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0.45,
                        "slider": True,
                    },
                ],
                "group3": [
                    {
                        "id": "e2D1",
                        "name": "2D-1",
                        "description": "Partial overlap with the 3’ untranslated region of an established HI gene",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                    {
                        "id": "e2D2",
                        "name": "2D-2",
                        "description": "Partial overlap with the last exon of an established HI gene. Other pathogenic variants have been reported in this exon.",
                        "score": 0,
                        "suggested": 0.90,
                        "min": 0.45,
                        "max": 0.90,
                        "slider": True,
                    },
                    {
                        "id": "e2D3",
                        "name": "2D-3",
                        "description": "Partial overlap with the last exon of an established HI gene. No other pathogenic variants have been reported in this exon.",
                        "score": 0,
                        "suggested": 0.30,
                        "min": 0,
                        "max": 0.45,
                        "slider": True,
                    },
                    {
                        "id": "e2D4",
                        "name": "2D-4",
                        "description": "Includes other exons in addition to the last exon. Nonsense-mediated decay is expected.",
                        "score": 0,
                        "suggested": 0.90,
                        "min": 0.45,
                        "max": 1.00,
                        "slider": True,
                    },
                ],
                "group4": [
                    {
                        "id": "e2E",
                        "name": "2E",
                        "description": "Both breakpoints are within the same gene (intragenic CNV; gene-level sequence variant)",
                        "score": 0,
                        "suggested": "null",
                        "ranges": {
                            "PVS1": [0.45, 0.90],
                            "PVS1_Strong": [0.30, 0.90],
                            "PVS1_Moderate": [0.15, 0.45],
                            "PVS1_Supporting": [0, 0.30],
                        },
                    },
                    {
                        "id": "e2F",
                        "name": "2F",
                        "description": "Completely contained within an established benign CNV region",
                        "score": 0,
                        "suggested": -1.00,
                        "min": -1.00,
                        "max": -1.00,
                    },
                    {
                        "id": "e2G",
                        "name": "2G",
                        "description": "Overlaps an established benign CNV, but includes additional genomic material",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                    {
                        "id": "e2H",
                        "name": "2H",
                        "description": "Two or more HI predictors suggest at least one gene in the interval is haploinsufficient",
                        "score": 0,
                        "suggested": 0.15,
                        "min": 0.15,
                        "max": 0.15,
                    },
                ],
            },
        },
        {
            "id": "section3",
            "name": "Section 3: Evaluation of Gene Number",
            "evidences": {
                "group1": [
                    {
                        "id": "e3A",
                        "name": "3A",
                        "description": "0-24 genes",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                    {
                        "id": "e3B",
                        "name": "3B",
                        "description": "25-34 genes",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.45,
                        "max": 0.45,
                    },
                    {
                        "id": "e3C",
                        "name": "3C",
                        "description": "35+ genes",
                        "score": 0,
                        "suggested": 0.90,
                        "min": 0.90,
                        "max": 0.90,
                    },
                ]
            },
        },
        {
            "id": "section4",
            "name": "Section 4: Detailed Evaluation of Genomic Content Using Cases from Published Literature, Public Databases, and/or Internal Lab Data",
            "evidences": {
                "group1": [
                    {
                        "id": "e4A",
                        "name": "4A",
                        "description": "Reported proband has a highly specific and relatively unique phenotype; confirmed de novo",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.15,
                        "max": 0.45,
                    },
                    {
                        "id": "e4B",
                        "name": "4B",
                        "description": "Reported proband has a highly specific phenotype; consistent with the gene/genomic region, not necessarily unique; confirmed de novo",
                        "score": 0,
                        "suggested": 0.30,
                        "min": 0,
                        "max": 0.45,
                    },
                    {
                        "id": "e4C",
                        "name": "4C",
                        "description": "Reported proband has a consistent phenotype but not highly specific; confirmed de novo",
                        "score": 0,
                        "suggested": 0.15,
                        "min": 0,
                        "max": 0.30,
                    },
                    {
                        "id": "e4D",
                        "name": "4D",
                        "description": "Reported proband has an inconsistent phenotype with the gene/genomic region",
                        "score": 0,
                        "suggested": -0.30,
                        "min": -0.30,
                        "max": 0,
                    },
                ],
                "group2": [
                    {
                        "id": "e4E",
                        "name": "4E",
                        "description": "Reported proband has a highly specific phenotype; inheritance is unknown",
                        "score": 0,
                        "suggested": 0.10,
                        "min": 0,
                        "max": 0.15,
                    },
                    {
                        "id": "e4F",
                        "name": "4F",
                        "description": "Segregation: 3-4 observed cases",
                        "score": 0,
                        "suggested": 0.15,
                        "min": 0,
                        "max": 0.45,
                    },
                    {
                        "id": "e4G",
                        "name": "4G",
                        "description": "Segregation: 5-6 observed cases",
                        "score": 0,
                        "suggested": 0.30,
                        "min": 0,
                        "max": 0.45,
                    },
                    {
                        "id": "e4H",
                        "name": "4H",
                        "description": "Segregation: 7+ observed cases",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0,
                        "max": 0.45,
                    },
                ],
                "group3": [
                    {
                        "id": "e4I",
                        "name": "4I",
                        "description": "Non-segregation: Variant not found in other affected family members",
                        "score": 0,
                        "suggested": -0.45,
                        "min": -0.45,
                        "max": 0,
                    },
                    {
                        "id": "e4J",
                        "name": "4J",
                        "description": "Variant found in unaffected family members with the proband's phenotype",
                        "score": 0,
                        "suggested": -0.30,
                        "min": -0.30,
                        "max": 0,
                    },
                    {
                        "id": "e4K",
                        "name": "4K",
                        "description": "Variant found in unaffected family members with a non-specific phenotype",
                        "score": 0,
                        "suggested": -0.15,
                        "min": -0.15,
                        "max": 0,
                    },
                    {
                        "id": "e4L",
                        "name": "4L",
                        "description": "Case-control evidence: Statistically significant increase in cases with a specific phenotype",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0,
                        "max": 0.45,
                    },
                ],
                "group4": [
                    {
                        "id": "e4M",
                        "name": "4M",
                        "description": "Case-control evidence: Statistically significant increase in cases with a non-specific phenotype",
                        "score": 0,
                        "suggested": 0.30,
                        "min": 0,
                        "max": 0.30,
                    },
                    {
                        "id": "e4N",
                        "name": "4N",
                        "description": "Case-control evidence: No significant difference between cases and controls",
                        "score": 0,
                        "suggested": -0.90,
                        "min": -0.90,
                        "max": 0,
                    },
                    {
                        "id": "e4O",
                        "name": "4O",
                        "description": "Overlap with common population variation",
                        "score": 0,
                        "suggested": -1.00,
                        "min": -1.00,
                        "max": 0,
                    },
                ],
            },
        },
        {
            "id": "section5",
            "name": "Section 5: Evaluation of Inheritance Pattern/Family History for Patient Being Studied",
            "evidences": {
                "group1": [
                    {
                        "id": "e5A",
                        "name": "5A",
                        "description": "Observed CNV is de novo",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.15,
                        "max": 0.45,
                    },
                    {
                        "id": "e5B",
                        "name": "5B",
                        "description": "Observed CNV is inherited; specific phenotype, no family history",
                        "score": 0,
                        "suggested": -0.30,
                        "min": -0.45,
                        "max": 0,
                    },
                    {
                        "id": "e5C",
                        "name": "5C",
                        "description": "Observed CNV is inherited; non-specific phenotype, no family history",
                        "score": 0,
                        "suggested": -0.15,
                        "min": -0.30,
                        "max": 0,
                    },
                    {
                        "id": "e5D",
                        "name": "5D",
                        "description": "Observed CNV segregates with a consistent phenotype in family",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.15,
                        "max": 0.45,
                    },
                ],
                "group2": [
                    {
                        "id": "e5E",
                        "name": "5E",
                        "description": "Non-segregation: Use appropriate scoring from Section 4",
                        "score": 0,
                        "suggested": -0.45,
                        "min": -0.45,
                        "max": 0,
                    },
                    {
                        "id": "e5F",
                        "name": "5F",
                        "description": "Inheritance information is unavailable or uninformative",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                    {
                        "id": "e5G",
                        "name": "5G",
                        "description": "Inheritance information unavailable; non-specific phenotype consistent with similar cases",
                        "score": 0,
                        "suggested": 0.10,
                        "min": 0,
                        "max": 0.15,
                    },
                    {
                        "id": "e5H",
                        "name": "5H",
                        "description": "Inheritance information unavailable; highly specific phenotype consistent with similar cases",
                        "score": 0,
                        "suggested": 0.30,
                        "min": 0,
                        "max": 0.30,
                    },
                ],
            },
        },
    ],
}

acmg_gain = {
    "type": "gain",
    "sections": [
        {
            "id": "section1",
            "name": "Section 1: Initial Assessment of Genomic Content",
            "evidences": {
                "group1": [
                    {
                        "id": "e1A",
                        "name": "1A",
                        "description": "Contains protein-coding or other known functionally important elements",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 1,
                    },
                    {
                        "id": "e1B",
                        "name": "1B",
                        "description": "Does NOT contain protein-coding or any known functionally important elements",
                        "score": 0,
                        "suggested": -0.60,
                        "min": -0.60,
                        "max": -0.60,
                    },
                ]
            },
        },
        {
            "id": "section2",
            "name": "Section 2: Overlap with Established Triplosensitive (TS), Haploinsufficient (HI), or Benign Genes or Genomic Regions",
            "evidences": {
                "group1": [
                    {
                        "id": "e2A",
                        "name": "2A",
                        "description": "Complete overlap of an established TS gene/genomic region",
                        "score": 0,
                        "suggested": 1.00,
                        "min": 0.00,
                        "max": 1.00,
                    },
                    {
                        "id": "e2B",
                        "name": "2B",
                        "description": "Partial overlap of an established TS region. Observed CNV does NOT contain the known causative gene/critical region OR unclear if affected OR no specific causative gene.",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                    {
                        "id": "e2C",
                        "name": "2C",
                        "description": "Identical in gene content to the established benign copy number gain",
                        "score": 0,
                        "suggested": -1.00,
                        "min": -1.00,
                        "max": -1.00,
                    },
                ]
            },
        },
        {
            "id": "section3",
            "name": "Section 3: Evaluation of Gene Number",
            "evidences": {
                "group1": [
                    {
                        "id": "e3A",
                        "name": "3A",
                        "description": "0-34 protein-coding RefSeq genes wholly or partially included in the gain",
                        "score": 0,
                        "suggested": 0,
                        "min": 0,
                        "max": 0,
                    },
                    {
                        "id": "e3B",
                        "name": "3B",
                        "description": "35-49 protein-coding RefSeq genes wholly or partially included in the gain",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.45,
                        "max": 0.45,
                    },
                    {
                        "id": "e3C",
                        "name": "3C",
                        "description": "50 or more protein-coding RefSeq genes wholly or partially included in the gain",
                        "score": 0,
                        "suggested": 0.90,
                        "min": 0.90,
                        "max": 0.90,
                    },
                ]
            },
        },
        {
            "id": "section4",
            "name": "Section 4: Detailed Evaluation of Genomic Content Using Cases from Published Literature, Public Databases, and/or Internal Lab Data",
            "evidences": {
                "group1": [
                    {
                        "id": "e4A",
                        "name": "4A",
                        "description": "Reported phenotype is highly specific and relatively unique to the gene or genomic region; confirmed de novo",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.15,
                        "max": 0.45,
                    },
                    {
                        "id": "e4B",
                        "name": "4B",
                        "description": "Reported phenotype is consistent with the gene/genomic region but not necessarily unique; confirmed de novo",
                        "score": 0,
                        "suggested": 0.30,
                        "min": 0,
                        "max": 0.45,
                    },
                    {
                        "id": "e4C",
                        "name": "4C",
                        "description": "Reported phenotype is consistent with the gene/genomic region but not highly specific; confirmed de novo",
                        "score": 0,
                        "suggested": 0.15,
                        "min": 0,
                        "max": 0.30,
                    },
                    {
                        "id": "e4D",
                        "name": "4D",
                        "description": "Reported phenotype is NOT consistent with the gene/genomic region or not consistent in general",
                        "score": 0,
                        "suggested": -0.30,
                        "min": -0.30,
                        "max": -0.30,
                    },
                ]
            },
        },
        {
            "id": "section5",
            "name": "Section 5: Evaluation of Inheritance Patterns/Family History for Patient Being Studied",
            "evidences": {
                "group1": [
                    {
                        "id": "e5A",
                        "name": "5A",
                        "description": "Observed copy number gain is DE NOVO",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.15,
                        "max": 0.45,
                    },
                    {
                        "id": "e5B",
                        "name": "5B",
                        "description": "Copy number gain is inherited from an unaffected parent; patient has specific phenotype",
                        "score": 0,
                        "suggested": -0.30,
                        "min": -0.30,
                        "max": -0.45,
                    },
                    {
                        "id": "e5C",
                        "name": "5C",
                        "description": "Copy number gain is inherited from an unaffected parent; patient has non-specific phenotype",
                        "score": 0,
                        "suggested": -0.15,
                        "min": -0.15,
                        "max": -0.30,
                    },
                    {
                        "id": "e5D",
                        "name": "5D",
                        "description": "CNV segregates with consistent phenotype observed in the patient’s family",
                        "score": 0,
                        "suggested": 0.45,
                        "min": 0.15,
                        "max": 0.45,
                    },
                ]
            },
        },
    ],
}


def get_default_gateway_linux():
    """Read the default gateway directly from /proc."""
    with open("/proc/net/route") as fh:
        for line in fh:
            fields = line.strip().split()
            if fields[1] != "00000000" or not int(fields[3], 16) & 2:
                # If not default route or not RTF_GATEWAY, skip it
                continue

            return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))


def get_version():
    with open(settings.BASE_DIR.joinpath(".git/ORIG_HEAD")) as f:
        return f.read().splitlines()[0][:6]


def start_run(chip_id):
    if not settings.DEBUG:
        HOST_IP = get_default_gateway_linux()
        MINIO_IP = socket.gethostbyname("minio")
        label = secrets.token_urlsafe(6)
        chipType = Chip.objects.get(chip_id=chip_id).chip_type

        # Create the sample sheet file
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as ss:
            ss.write(f"sample_id\tprotocol_id\tinstitution\n")
            for cs in ChipSample.objects.filter(chip__chip_id=chip_id):
                ss.write(
                    f"{chip_id}_{cs.position}\t{cs.sample.protocol_id}\t{cs.sample.institution.name}\n"
                )
            ss.flush()
            samplesheet_path = ss.name

        # Create the Nextflow configuration file
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as nfc:
            nfc.write(
                f"""aws {{
  access_key = "{settings.MINIO_STORAGE_ACCESS_KEY}"
  secret_key = "{settings.MINIO_STORAGE_SECRET_KEY}"
  client {{
    endpoint = "http://{MINIO_IP}:9000"
  }}
}}
profiles {{
  docker {{
    docker.enabled = true
  }}
}}"""
            )
            nfc.flush()
            nextflow_config_path = nfc.name

        # Create the script to execute on the host
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as script:
            script.write(
                f"""#!/bin/bash
export TS_SOCKET="~/ts_start_run.socket"
mkdir {label} && cd {label}
tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \\
    --chip_id {chip_id} \\
    --bpm s3://canvas/{chipType.bpm.name} \\
    --csv s3://canvas/{chipType.csv.name} \\
    --egt s3://canvas/{chipType.egt.name} \\
    --fasta s3://canvas/{chipType.fasta.name} \\
    --pfb s3://canvas/{chipType.pfb.name} \\
    --band s3://canvas/{chipType.band.name} \\
    --tex_template canvas-pipeline/template/base_template.tex \\
    --output_dir canvas-pipeline-demo-results/ \\
    --samplesheet /tmp/samplesheet.tsv \\
    -c /tmp/nextflow.config \\
    -with-report {chip_id}_{label}.html \\
    -profile docker

tsp -D $(tsp -l | grep {label} | cut -d' ' -f1) docker compose \\
    -f /home/canvas/canvas/docker-compose_prod.yaml \\
    exec canvas \\
    python manage.py associate_files {chip_id} canvas
"""
            )
            script.flush()
            script_path = script.name

        # Transfer the files to the host
        for file_path in [samplesheet_path, nextflow_config_path, script_path]:
            subprocess.run(
                f"scp {file_path} canvas@{HOST_IP}:/tmp",
                shell=True,
            )

        # Execute the script on the host
        subprocess.run(
            f"ssh canvas@{HOST_IP} 'chmod +x {script_path} && {script_path}'",
            shell=True,
        )
        # Clean up the local temporary files
        os.remove(samplesheet_path)
        os.remove(nextflow_config_path)
        os.remove(script_path)


def get_samples_for_user(user, samples=None):
    if not samples:
        samples = Sample.objects.all()
    user_groups = user.groups.all()
    if user.is_staff:
        samples = samples
    else:
        samples = samples.filter(institution__group__in=user_groups)
    return samples


def get_chips_for_user(user, chips=None):
    if not chips:
        chips = Chip.objects.all()

    user_groups = user.groups.all()
    if user.is_staff:
        chips = chips
    else:
        chips = chips.filter(
            chipsample__sample__institution__group__in=user_groups
        ).distinct()
    return chips


def get_institutions_for_user(user, institutions=None):
    if not institutions:
        institutions = Institution.objects.all()

    user_groups = user.groups.all()
    if user.is_staff:
        institutions = institutions
    else:
        institutions = institutions.filter(group__in=user_groups)
    return institutions


def index(request):
    samples = get_samples_for_user(request.user).order_by("-entry_date")
    len_samples = len(samples)
    sample_paginator = Paginator(samples, 12)
    samples = sample_paginator.get_page(1)

    chips = get_chips_for_user(request.user).order_by("-entry_date")
    len_chips = len(chips)
    chip_paginator = Paginator(chips, 12)
    chips = chip_paginator.get_page(1)

    label = secrets.token_urlsafe(6)
    return render(
        request,
        "canvas/index.html",
        {
            "title": "Index",
            "label": label,
            "samples": samples,
            "len_samples": len_samples,
            "chips": chips,
            "len_chips": len_chips,
            "canvas_version": get_version(),
            "acmg_loss": acmg_loss,
        },
    )


@login_required
def generic_search(request, model_name, field_name):
    query = request.GET.get("search", "").strip()
    page = request.GET.get("page")

    # Dynamically get the model class
    model = apps.get_model(app_label="canvas", model_name=model_name)

    if query:
        # Use the field_name dynamically
        filter_kwargs = {f"{field_name}__icontains": query}
        items = model.objects.filter(**filter_kwargs)
    else:
        items = model.objects.none()

    if model_name == "Sample":
        items = get_samples_for_user(request.user, items)
    if model_name == "Chip":
        items = get_chips_for_user(request.user, items)
    if model_name == "Institution":
        items = get_institutions_for_user(request.user, items)

    items = items.order_by(field_name)
    len_items = len(items)

    paginator = Paginator(items, 12)
    items = paginator.get_page(page)

    return render(
        request,
        "canvas/partials/search_results.html",
        {
            "items": items,
            "len_items": len_items,
            "query": query,
            "model_name": model_name,
            "field_name": field_name,
        },
    )


@login_required
def chip_search(request):
    query = request.GET.get("search", "").strip()
    page = request.GET.get("page")

    chips = Chip.objects.filter(chip_id__contains=query).order_by("-entry_date")
    chips = get_chips_for_user(request.user, chips)

    len_chips = len(chips)

    paginator = Paginator(chips, 12)
    chips = paginator.get_page(page)

    return render(
        request,
        "canvas/partials/chips.html",
        {"chips": chips, "query": query, "len_chips": len_chips},
    )


@login_required
def sample_search(request):
    query = request.GET.get("search", "")
    page = request.GET.get("page")
    institutions = request.GET.getlist("institutions")
    chips = request.GET.getlist("chips")
    # hack  for searching unicode chars in protocol_ids
    query = query.upper()

    # Start with filtering by protocol ID
    samples = Sample.objects.filter(protocol_id__icontains=query)

    # Filter by institutions if any are selected
    if institutions:
        samples = samples.filter(institution__name__in=institutions)

    # Assuming a relationship exists, filter by chips
    if chips:
        samples = samples.filter(chipsample__chip__chip_id__in=chips)

    # Order by entry date
    samples = samples.order_by("-entry_date")
    samples = get_samples_for_user(request.user, samples)
    len_samples = len(samples)

    paginator = Paginator(samples, 12)
    samples = paginator.get_page(page)
    return render(
        request,
        "canvas/partials/sample_results.html",
        {"samples": samples, "query": query, "len_samples": len_samples},
    )


@login_required
def chipsample_tab_button(request):
    chipsample_pk = request.GET.get("chipsample_pk")
    chipsample = ChipSample.objects.get(id=chipsample_pk)
    return render(
        request,
        "canvas/partials/chipsample_tab_button.html",
        {"chipsample": chipsample},
    )


@login_required
def chipsample_tab_content(request):
    chipsample_pk = request.GET.get("chipsample_pk")
    chipsample = ChipSample.objects.get(id=chipsample_pk)

    cnvs = []
    for cnv in chipsample.cnv.all():
        cnv_json = cnv.cnv_json
        cnv_json["cnv_pk"] = cnv.pk
        cnv_json["total_score"] = cnv_json.pop("Total score", None)
        cnv_json["addToReport"] = False
        cnvs.append(cnv_json)

    bedgraphs = chipsample.bedgraph.all()
    lrr_bedgraph = None
    baf_bedgraph = None
    cnv_pos_bedgraph = None
    cnv_neg_bedgraph = None
    lrr_smooth_bedgraph = None

    for bedgraph in bedgraphs:
        if bedgraph.bedgraph_type == "LRR":
            lrr_bedgraph = bedgraph
        elif bedgraph.bedgraph_type == "BAF":
            baf_bedgraph = bedgraph
        elif bedgraph.bedgraph_type == "CNV_pos":
            cnv_pos_bedgraph = bedgraph
        elif bedgraph.bedgraph_type == "CNV_neg":
            cnv_neg_bedgraph = bedgraph
        elif bedgraph.bedgraph_type == "LRR_smooth":
            lrr_smooth_bedgraph = bedgraph

    return render(
        request,
        "canvas/partials/chipsample_tab_content.html",
        {
            "chipsample": chipsample,
            "lrr_bedgraph": lrr_bedgraph,
            "baf_bedgraph": baf_bedgraph,
            "cnv_pos_bedgraph": cnv_pos_bedgraph,
            "cnv_neg_bedgraph": cnv_neg_bedgraph,
            "lrr_smooth_bedgraph": lrr_smooth_bedgraph,
            "cnvs": json.dumps(cnvs),
        },
    )


@login_required
def chipsample_sidebar_content(request):
    chipsample_pk = request.GET.get("chipsample_pk")
    chipsample = ChipSample.objects.get(id=chipsample_pk)
    return render(
        request,
        "canvas/partials/chipsample_sidebar_content.html",
        {"chipsample": chipsample},
    )


@login_required
def sample_edit(request):
    if request.method == "GET":
        sample_pk = request.GET.get("sample_pk")
        sample = Sample.objects.get(id=sample_pk)
        return render(
            request, "canvas/partials/sample_edit.html", {"sample": sample}
        )  # For debugging

    if request.method == "POST":
        sample_pk = request.POST.get("sample_pk")
        sample = Sample.objects.get(id=sample_pk)
        edit = request.POST.get("edit", None)
        if edit == "false":
            return render(request, "canvas/partials/sample.html", {"sample": sample})

        protocol_id = request.POST.get("protocol_id")
        arrival_date = request.POST.get("arrival_date")
        scan_date = request.POST.get("scan_date")
        sex = request.POST.get("sex")
        sample_type_id = request.POST.get("SampleType")
        repeat_id = request.POST.get("Sample")

        sample_type = SampleType.objects.get(pk=sample_type_id)
        if repeat_id:
            repeat = Sample.objects.filter(pk=int(repeat_id)).first()
        else:
            repeat = None

        sample.protocol_id = protocol_id
        sample.arrival_date = parse_date(arrival_date)
        sample.scan_date = parse_date(scan_date)
        sample.sex = sex
        sample.sample_type = sample_type
        sample.repeat = repeat
        sample.save()

        return render(request, "canvas/partials/sample.html", {"sample": sample})


@login_required
def chip_edit(request):
    if request.method == "GET":
        chip_pk = request.GET.get("chip_pk")
        chip = Chip.objects.get(id=chip_pk)
        return render(
            request, "canvas/partials/chip_edit.html", {"chip": chip}
        )  # For debugging

    if request.method == "POST":
        chip_pk = request.POST.get("chip_pk")
        chip = Chip.objects.get(id=chip_pk)

        edit = request.POST.get("edit", None)
        if edit == "false":
            return render(
                request, "canvas/partials/chip.html", {"chip": chip}
            )  # For debugging

        positions = request.POST.getlist("position")
        samples = request.POST.getlist("Sample")

        for position, sample_pk in zip(positions, samples):
            if sample_pk.strip():
                sample = Sample.objects.get(pk=int(sample_pk))

                chipsample = ChipSample.objects.filter(
                    chip=chip, position=position
                ).first()
                if chipsample:
                    chipsample.sample = sample
                    chipsample.save()
                else:
                    ChipSample.objects.create(
                        chip=chip, position=position, sample=sample
                    )

        if not chipsample.call_rate:
            start_run(chip.chip_id)
        return render(request, "canvas/partials/chip.html", {"chip": chip})


@login_required
def get_sample_input_row(request):
    return render(request, "canvas/partials/sample_input_row.html")


@login_required
def save_samples(request):
    form_data = dict(request.POST)

    form_invalid = False
    errors = []

    len_samples = len(form_data["protocol_id"])
    # Process each row of form data
    try:
        with transaction.atomic():
            for i in range(len(form_data["protocol_id"])):
                try:
                    institution = Institution.objects.get(
                        id=form_data["Institution"][i]
                    )
                    sample_type = SampleType.objects.get(id=form_data["SampleType"][i])

                    # Create new Sample object
                    sample = Sample.objects.create(
                        arrival_date=form_data["arrival_date"][i],
                        study_date=form_data["study_date"][i] or None,
                        protocol_id=form_data["protocol_id"][i],
                        concentration=form_data["concentration"][i],
                        institution=institution,
                        sex=form_data["sex"][i],
                        description=form_data["description"][i],
                        sample_type=sample_type,
                    )

                    # Handle ManyToManyField 'repeat'
                    repeat_sample_id = form_data["Sample"][i]
                    if repeat_sample_id:
                        try:
                            repeat_sample = Sample.objects.get(id=repeat_sample_id)
                            sample.repeat.add(repeat_sample)
                        except Sample.DoesNotExist:
                            errors.append(
                                f"Sample with ID {repeat_sample_id} not found."
                            )
                            form_invalid = True

                except Institution.DoesNotExist:
                    errors.append(
                        f"Institution with ID {form_data['Institution'][i]} not found."
                    )
                    form_invalid = True
                except SampleType.DoesNotExist:
                    errors.append(
                        f"SampleType with ID {form_data['SampleType'][i]} not found."
                    )
                    form_invalid = True

        # Check if the form is invalid after the loop
        if form_invalid:
            return render(
                request,
                "canvas/partials/sample_results.html",
                {"errors": errors},
            )
        else:
            # If everything is successful, return success response
            response = render(
                request,
                "canvas/partials/sample_input_results.html",
                {"len_samples": len_samples},
            )
            return retarget(response, "#sample-input-form")

    except Exception as e:
        errors.append(str(e))
        return render(
            request,
            "canvas/partials/sample_input_results.html",
            {"errors": errors},
        )


def create_report(request):
    cnvs = json.loads(request.POST.get("cnvs"))
    cnvs = {cnv["VariantID"]: cnv for cnv in cnvs}
    classification_ids = []
    for variant_id, cnv in cnvs.items():
        if "classification_pk" in cnv.keys():
            classification_ids.append(cnv["classification_pk"])
            classification = Classification.objects.get(pk=cnv["classification_pk"])
            cnv.update(classification.classification_json)
            cnv["Classification"] = classification.classification_json["classification"]
            cnv["Total score"] = classification.classification_json["total_score"]
        else:
            cnv["Total score"] = cnv["total_score"]
    chipsample_pk = request.POST.get("chipsample_pk")
    chipsample = ChipSample.objects.get(id=chipsample_pk)
    chip_id = chipsample.chip.chip_id
    chip_type = chipsample.chip.chip_type.name
    version = get_version()

    if not settings.DEBUG:
        HOST_IP = get_default_gateway_linux()
        MINIO_IP = socket.gethostbyname("minio")
        label = secrets.token_urlsafe(6)

        # Create temporary files
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as cnv_file:
            json.dump(cnvs, cnv_file)
            cnv_file.flush()
            cnv_file_path = cnv_file.name

        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as nfc:
            nfc.write(
                f"""aws {{
    access_key = "{settings.MINIO_STORAGE_ACCESS_KEY}"
    secret_key = "{settings.MINIO_STORAGE_SECRET_KEY}"
    client {{
    endpoint = "http://{MINIO_IP}:9000"
    }}
    }}
    profiles {{
    docker {{
        docker.enabled = true
    }}
    }}"""
            )
            nfc.flush()
            nfc_path = nfc.name

        # Create the script file
        with tempfile.NamedTemporaryFile(
            delete_on_close=False, mode="w"
        ) as script_file:
            script_file.write(
                f"""#!/bin/bash
export TS_SOCKET="~/ts_create_report.socket"
mkdir {label}
cd {label}
tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \\
    --chip_id {chip_id} \\
    --chip_type {chip_type} \\
    --position {chipsample.position} \\
    --tex_template canvas-pipeline/template/base_template.tex \\
    --cnvs {cnv_file_path} \\
    --institute "{chipsample.sample.institution.name}" \\
    --protocol_id "{chipsample.sample.protocol_id}" \\
    --version {version} \\
    -c {nfc_path} \\
    -with-report {chip_id}_{label}.html \\
    -profile docker
tsp -f -D $(tsp -l | grep {label} | cut -d" " -f1) docker compose \\
    -f /home/canvas/canvas/docker-compose_prod.yaml \\
    exec canvas python manage.py associate_files --pdf {chip_id} canvas \\
    {"--classification_ids " + " ".join(map(str, classification_ids)) if classification_ids else ""}
            """
            )
            script_file.flush()
            script_path = script_file.name

        # Transfer files to host
        for local_path in [cnv_file_path, nfc_path, script_path]:
            subprocess.run(f"scp {local_path} canvas@{HOST_IP}:/tmp", shell=True)

        # Execute the script on the host
        subprocess.run(
            f"ssh canvas@{HOST_IP} 'chmod +x {script_path} && {script_path}'",
            shell=True,
        )

        # Clean up temporary files
        os.remove(cnv_file_path)
        os.remove(nfc_path)
        os.remove(script_path)

    context = {
        "reports": gather_reports(chipsample),
        "button": "true",
        "chipsample": chipsample,
    }
    response = render(request, "canvas/partials/report_list.html", context=context)
    response["HX-Trigger"] = f"triggerReportUpdate{chipsample_pk}"
    return response


def get_report_count(request):
    chipsample_pk = request.POST.get("chipsample_pk")
    chipsample = ChipSample.objects.get(id=chipsample_pk)
    button = request.POST.get("button")
    return render(
        request,
        "canvas/partials/report_summary.html",
        context={
            "chipsample": chipsample,
            "button": button,
        },
    )


def gather_reports(chipsample):
    return Report.objects.filter(chipsample=chipsample).order_by("-entry_date")


def get_reports(request):
    chipsample_pk = request.POST.get("chipsample_pk")
    button = request.POST.get("button")
    chipsample = ChipSample.objects.get(id=chipsample_pk)

    context = {
        "reports": gather_reports(chipsample),
        "button": button,
        "chipsample": chipsample,
    }
    return render(request, "canvas/partials/report_list.html", context=context)


def idat_upload(request):
    if request.method == "POST":
        files = request.FILES.getlist("files")
        uploaded_files = []
        errors = []

        chip_type_pk = request.POST.get("ChipType")
        chip_type = ChipType.objects.get(pk=int(chip_type_pk[0]))

        for file in files:
            if file.name.endswith(".idat"):
                try:
                    chip_id, position = file.name.split("_")[:2]
                    chip, created = Chip.objects.get_or_create(
                        chip_id=chip_id,
                        defaults={
                            "chip_type": chip_type,
                            "lab_practitioner": request.user,
                            "protocol_start_date": timezone.now(),
                            "scan_date": timezone.now(),
                        },
                    )
                    chipsample, created = ChipSample.objects.get_or_create(
                        chip=chip, position=position
                    )
                    idat_file = IDAT.objects.create(idat=file, chipsample=chipsample)
                    uploaded_files.append(idat_file)
                except Exception as e:
                    errors.append(f"Error uploading {file.name}: {str(e)}")
            else:
                errors.append(f"Invalid file type: {file.name}")

        # Render the uploaded files and error messages into HTML
        context = {"uploaded_files": uploaded_files, "errors": errors}
        return render(request, "canvas/partials/idat_upload_results.html", context)


def upload_tsv(request):
    tsv_file = request.FILES.get("tsv_file")
    file_path = "/tmp/uploaded_file.tsv"
    with open(file_path, "wb+") as destination:
        for chunk in tsv_file.chunks():
            destination.write(chunk)
    sample_list = read_sample_from_tsv(file_path)
    context = {"sample_list": sample_list}
    return render(request, "canvas/partials/samples_from_tsv.html", context)


def get_evidences(cnv):
    cnv_type = cnv.cnv_json["Type"]
    if cnv_type == "DEL":
        acmg = acmg_loss
    elif cnv_type == "DUP":
        acmg = acmg_gain
    else:
        # TODO: handle it better
        acmg = acmg_loss

    for section in acmg["sections"]:
        for group, evidences in section["evidences"].items():
            for evidence in evidences:
                if cnv.cnv_json["1A-B"] == "0.0" and evidence["name"] == "1A":
                    evidence["score"] = 0.0
                if cnv.cnv_json["1A-B"] == "-0.6" and evidence["name"] == "1B":
                    evidence["score"] = -0.6
                if cnv.cnv_json["3"] == "0.0" and evidence["name"] == "3A":
                    evidence["score"] = 0.0
                if cnv.cnv_json["3"] == "0.45" and evidence["name"] == "3B":
                    evidence["score"] = 0.45
                if cnv.cnv_json["3"] == "0.9" and evidence["name"] == "3C":
                    evidence["score"] = 0.9
                try:
                    evidence["score"] = float(cnv.cnv_json[evidence["name"]])
                except KeyError:
                    pass
    return acmg


def get_acmg(request):
    if request.method == "POST":
        cnv_pk = request.POST.get("cnv_pk")
        cnv = CNV.objects.get(pk=cnv_pk)
        cnv.cnv_json["total_score"] = cnv.cnv_json.pop("Total score", None)
        acmg = get_evidences(cnv)
    return render(
        request,
        "canvas/components/variant_modal.html",
        {"cnv": cnv, "acmg": acmg},
    )


def save_acmg(request):
    if request.method == "POST":
        form_data = dict(request.POST)
        cnv_data = {k: v[0] for k, v in form_data.items()}
        cnv = CNV.objects.get(pk=cnv_data["cnv_pk"])
        acmg_loss = get_evidences(cnv)
        classification = Classification.objects.create(
            cnv=cnv,
            user=request.user,
            classification_json=cnv_data,
        )
        context = {
            "cnv": cnv,
            "classification": classification,
            "acmg_loss": acmg_loss,
            "success": "True",
        }
    return render(request, "canvas/components/variant_modal.html", context)


def get_cnv_modal(request):
    if request.method == "POST":
        chipsample_pk = request.POST.get("chipsample_pk")
        chipsample = ChipSample.objects.get(id=chipsample_pk)

        # Parse ROIs from the POST request
        rois = request.POST.get("rois", "[]")
        try:
            rois = json.loads(rois)
        except json.JSONDecodeError:
            rois = []

        # Initialize dictionary to store CNVs by ROI
        intersecting_cnvs_by_roi = {roi: [] for roi in rois}

        # Prepare ROI ranges
        roi_ranges = {}
        for roi in rois:
            try:
                chromosome, positions = roi.split(":")
                start, end = map(int, positions.split("-"))
                roi_ranges[roi] = (chromosome, start, end)
            except ValueError:
                continue

        # Get all CNVs for this chipsample
        cnvs = CNV.objects.filter(chipsample=chipsample)

        # Check each CNV against each ROI
        for cnv in cnvs:
            chr_info = cnv.cnv_json.get("chr_info")
            if not chr_info:
                continue

            try:
                cnv_chromosome, positions = chr_info.split(":")
                cnv_start, cnv_end = map(int, positions.split("-"))
            except ValueError:
                continue

            # Check intersection with each ROI
            for roi, (roi_chromosome, roi_start, roi_end) in roi_ranges.items():
                if (
                    cnv_chromosome == roi_chromosome
                    and roi_start <= cnv_end
                    and roi_end >= cnv_start
                ):
                    intersecting_cnvs_by_roi[roi].append(cnv)

        context = {
            "chipsample": chipsample,
            "rois": rois,
            "intersecting_cnvs_by_roi": intersecting_cnvs_by_roi,
            "showModal": True,
        }
        return render(request, "canvas/components/cnv_modal.html", context)


@login_required
def cnv_edit(request):
    if request.method == "POST":
        chipsample_pk = request.POST.get("chipsample_pk")
        roi = request.POST.get("roi")  # Format: chr:start-end
        cn = request.POST.get("copy_number", "2")  # Default to CN=2 (normal)
        snap_probes = request.POST.get("snap_probes", "on").lower() == "on"

        try:
            # Parse ROI
            chromosome, positions = roi.split(":")
            start, end = map(int, positions.split("-"))

            # Create new CNV
            chipsample = ChipSample.objects.get(id=chipsample_pk)
            cnv = CNV.objects.create(
                chipsample=chipsample,
                user=request.user,
                cnv_json={"user_cnv": roi, "user_copy_number": cn},
            )

            chip_id = chipsample.chip.chip_id

            if not settings.DEBUG:
                HOST_IP = get_default_gateway_linux()
                MINIO_IP = socket.gethostbyname("minio")
                label = secrets.token_urlsafe(6)

                # Write CNV data to temporary file
                cnv_file_path = None
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as cnv_file:
                    cnv_file_path = cnv_file.name
                    cnv_file.write(f"{chromosome}\t{start}\t{end}\t{cn}\n")

                # Create Nextflow config file
                nfc_file_path = None
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as nfc:
                    nfc_file_path = nfc.name
                    nfc.write(
                        f"""aws {{
    access_key = "{settings.MINIO_STORAGE_ACCESS_KEY}"
    secret_key = "{settings.MINIO_STORAGE_SECRET_KEY}"
    client {{
    endpoint = "http://{MINIO_IP}:9000"
    }}
    }}
    profiles {{
    docker {{
        docker.enabled = true
    }}
    }}"""
                    )

                # Write commands to script file
                script_path = None
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as script_file:
                    script_path = script_file.name
                    script_file.write(
                        f"""#!/bin/bash
export TS_SOCKET="~/ts_cnv_edit.socket"
mkdir {label} && cd {label}
tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \\
    --chip_id {chip_id} \\
    --position {chipsample.position} \\
    --cnv_bed {cnv_file_path} \\
    --snap_probes {snap_probes} \\
    --cnv_pk {cnv.pk} \\
    --band s3://canvas/analysis_files/GSA-Cyto/hg19_chrom_band.txt \\
    -c {nfc_file_path} \\
    -profile docker
tsp -f -D $(tsp -l | grep {label} | cut -d" " -f1) docker compose \\
    -f /home/canvas/canvas/docker-compose_prod.yaml \\
    exec canvas \\
    python manage.py associate_files --cnv_pk {cnv.pk} {chip_id} canvas
"""
                    )

                # Transfer the files to the host
                for file_path in [cnv_file_path, nfc_file_path, script_path]:
                    subprocess.run(
                        f"scp {file_path} canvas@{HOST_IP}:/tmp",
                        shell=True,
                    )

                # Transfer and execute script
                subprocess.run(
                    f"ssh canvas@{HOST_IP} 'chmod +x {script_path} && {script_path}'",
                    shell=True,
                )

            return render(
                request,
                "canvas/partials/cnv_edit_success.html",
                {
                    "success": True,
                    "message": "CNV successfully added",
                    "cnv": json.dumps(cnv.cnv_json),
                },
            )

        except Exception as e:
            return render(
                request,
                "canvas/partials/cnv_edit_success.html",
                {"success": False, "message": str(e)},
            )

    return render(
        request,
        "canvas/partials/cnv_edit_success.html",
        {"success": False, "message": "Invalid request method"},
    )


def download_samples(request):
    # Get the filters from request
    try:
        # Decode JSON strings into Python lists
        institutions = json.loads(request.GET.get("institutions", "[]"))
        chips = json.loads(request.GET.get("chips", "[]"))
    except json.JSONDecodeError:
        institutions = []
        chips = []

    query = (
        request.GET.get("search", "").strip().upper()
    )  # Match the search logic from sample_search view

    # Start with all samples
    samples = Sample.objects.all()

    # Apply filters
    if query:
        samples = samples.filter(protocol_id__icontains=query)

    if institutions:
        samples = samples.filter(institution__name__in=institutions)

    if chips:
        samples = samples.filter(chipsample__chip__chip_id__in=chips)

    # Apply user permissions
    samples = get_samples_for_user(request.user, samples)

    # Create the HTTP response with CSV file
    response = HttpResponse(
        content_type="text/tab-separated-values",
        headers={"Content-Disposition": 'attachment; filename="samples.tsv"'},
    )

    # Create the TSV writer
    writer = csv.writer(response, delimiter="\t")

    # Write headers
    writer.writerow(
        [
            "Sample ID",
            "Institution",
            "Sample Type",
            "Sex",
            "Description",
            "Arrival Date",
            "Study Date",
            "Concentration",
            "Chip",
            "Position",
            "Scan Date",
            "Call Rate",
            "Autosomal Call Rate",
            "LRR StdDev",
            "Sex Estimate",
        ]
    )

    # Write data rows
    for sample in samples:
        chipsamples = sample.chipsample.all()
        if chipsamples:
            for cs in chipsamples:
                writer.writerow(
                    [
                        sample.protocol_id,
                        sample.institution.name,
                        sample.sample_type.name,
                        sample.sex,
                        sample.description,
                        sample.arrival_date,
                        sample.study_date,
                        sample.concentration,
                        cs.chip.chip_id if cs.chip else "",
                        cs.position if cs else "",
                        cs.chip.scan_date if cs.chip else "",
                        f"{cs.call_rate:.2%}" if cs.call_rate else "",
                        (
                            f"{cs.autosomal_call_rate:.2%}"
                            if cs.autosomal_call_rate
                            else ""
                        ),
                        f"{cs.lrr_std_dev:.4f}" if cs.lrr_std_dev else "",
                        cs.sex_estimate if cs else "",
                    ]
                )
        else:
            # Write sample info even if no chipsamples exist
            writer.writerow(
                [
                    sample.protocol_id,
                    sample.institution.name,
                    sample.sample_type.name,
                    sample.sex,
                    sample.description,
                    sample.arrival_date,
                    sample.study_date,
                    sample.concentration,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",  # Empty values for chip-related fields
                ]
            )

    return response


@login_required
def match_chip_samples(request):
    if request.method == "POST":
        chip_pk = request.POST.get("chip_pk")
        pasted_data = json.loads(request.POST.get("pasted_data"))
        chip = Chip.objects.get(id=chip_pk)

        # Process pasted data and find matches
        matches = {}
        for row in pasted_data:
            if len(row) >= 3:  # Ensure row has chip_id, position, protocol_id
                chip_id = row[0].strip()
                position = row[1].strip()
                protocol_id = row[2].strip()

                # Only process if chip_id matches
                if chip_id == chip.chip_id:
                    # Try to find matching sample
                    sample = Sample.objects.filter(
                        protocol_id__icontains=protocol_id
                    ).first()
                    if sample:
                        matches[position] = sample

        print(matches)
        return render(
            request,
            "canvas/partials/chip_edit.html",
            {"chip": chip, "matches": matches},
        )


def create_zip_response(files, filename):
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_obj in files:
            file_name = os.path.basename(file_obj.name)
            zip_file.writestr(file_name, file_obj.read())
    zip_buffer.seek(0)
    response = FileResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def download_chip_files(request, chip_id, file_type):
    # Validate file_type
    valid_types = ["idats", "gtcs", "vcfs", "bedgraphs", "reports", "all"]
    if file_type not in valid_types:
        raise Http404(f"Invalid file type: {file_type}")

    chip = get_object_or_404(Chip, id=chip_id)
    files = []

    for chipsample in chip.chipsample.all():
        # Check permissions
        if (
            chipsample.sample
            and not request.user.groups.filter(
                institutions=chipsample.sample.institution
            ).exists()
        ):
            continue

        # Get requested files based on file_type
        if file_type == "idats" or file_type == "all":
            files.extend([idat.idat for idat in chipsample.idat_set.all()])
        if file_type == "gtcs" or file_type == "all":
            files.extend([gtc.gtc for gtc in chipsample.gtc_set.all()])
        if file_type == "vcfs" or file_type == "all":
            files.extend([vcf.vcf for vcf in chipsample.vcf_set.all()])
        if file_type == "bedgraphs" or file_type == "all":
            files.extend([bg.bedgraph for bg in chipsample.bedgraph.all()])
        if file_type == "reports" or file_type == "all":
            files.extend([report.report for report in chipsample.report.all()])

    if not files:
        return HttpResponseForbidden(f"No accessible {file_type} files found")

    return create_zip_response(files, f"{chip.chip_id}_{file_type}.zip")


@login_required
def download_chipsample_files(request, chipsample_id, file_type):
    # Validate file_type
    valid_types = ["idats", "gtcs", "vcfs", "bedgraphs", "reports", "all"]
    if file_type not in valid_types:
        raise Http404(f"Invalid file type: {file_type}")

    chipsample = get_object_or_404(ChipSample, id=chipsample_id)

    # Check permissions
    if (
        chipsample.sample
        and not request.user.groups.filter(
            institutions=chipsample.sample.institution
        ).exists()
    ):
        return HttpResponseForbidden("No permission to access these files")

    files = []

    # Get requested files based on file_type
    if file_type == "idats" or file_type == "all":
        files.extend([idat.idat for idat in chipsample.idat_set.all()])
    if file_type == "gtcs" or file_type == "all":
        files.extend([gtc.gtc for gtc in chipsample.gtc_set.all()])
    if file_type == "vcfs" or file_type == "all":
        files.extend([vcf.vcf for vcf in chipsample.vcf_set.all()])
    if file_type == "bedgraphs" or file_type == "all":
        files.extend([bg.bedgraph for bg in chipsample.bedgraph.all()])
    if file_type == "reports" or file_type == "all":
        files.extend([report.report for report in chipsample.report.all()])

    if not files:
        return HttpResponseForbidden(f"No accessible {file_type} files found")

    return create_zip_response(files, f"chipsample_{chipsample_id}_{file_type}.zip")
