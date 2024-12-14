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


acmg_loss = [
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
]

acmg_gain = [
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
]


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

        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as ss:
            ss.write(f"sample_id\tprotocol_id\tinstitution\n")
            for cs in ChipSample.objects.filter(chip__chip_id=chip_id):
                ss.write(
                    f"{chip_id}_{cs.position}\t{cs.sample.protocol_id}\t{cs.sample.institution.name}\n"
                )
            ss.flush()
            subprocess.run(
                f"scp {ss.name} canvas@{HOST_IP}:/tmp/",
                shell=True,
            )

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
            subprocess.run(
                f"scp {nfc.name} canvas@{HOST_IP}:/tmp/",
                shell=True,
            )

        subprocess.run(
            f"ssh canvas@{HOST_IP} tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \
                                                --chip_id {chip_id} \
                                                --bpm s3://canvas/{chipType.bpm.name} \
                                                --csv s3://canvas/{chipType.csv.name} \
                                                --egt s3://canvas/{chipType.egt.name} \
                                                --fasta s3://canvas/{chipType.fasta.name} \
                                                --pfb s3://canvas/{chipType.pfb.name} \
                                                --band s3://canvas/{chipType.band.name} \
                                                --tex_template canvas-pipeline/template/base_template.tex \
                                                --output_dir canvas-pipeline-demo-results/ \
                                                --samplesheet {ss.name} \
                                                -c {nfc.name} \
                                                -with-report {chip_id}_{label}.html \
                                                -profile docker",
            shell=True,
        )
        subprocess.run(
            f"ssh canvas@{HOST_IP} 'tsp -D $(tsp -l | grep {label} | cut -d\" \" -f1) docker compose \
                                   -f /home/canvas/canvas/docker-compose_prod.yaml \
                                   exec canvas \
                                   python manage.py associate_files {chip_id} canvas'",
            shell=True,
        )


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

        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as cnv_file:
            json.dump(cnvs, cnv_file)
            cnv_file.flush()
            subprocess.run(
                f"scp {cnv_file.name} canvas@{HOST_IP}:/tmp/",
                shell=True,
            )

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
            subprocess.run(
                f"scp {nfc.name} canvas@{HOST_IP}:/tmp/",
                shell=True,
            )

        subprocess.run(
            f'ssh canvas@{HOST_IP} tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \
                                                --chip_id {chip_id} \
                                                --chip_type {chip_type} \
                                                --position {chipsample.position} \
                                                --tex_template canvas-pipeline/template/base_template.tex \
                                                --cnvs {cnv_file.name} \
                                                --institute "\\"{chipsample.sample.institution.name}\\"" \
                                                --protocol_id "\\"{chipsample.sample.protocol_id}\\"" \
                                                --version {version} \
                                                -c {nfc.name} \
                                                -with-report {chip_id}_{label}.html \
                                                -profile docker',
            shell=True,
        )
        classification_ids_arg = ""
        if classification_ids:
            classification_ids_arg = "--classification_ids " + " ".join(
                map(str, classification_ids)
            )
        subprocess.run(
            f"ssh canvas@{HOST_IP} 'tsp -f -D $(tsp -l | grep {label} | cut -d\" \" -f1) docker compose \
                                    -f /home/canvas/canvas/docker-compose_prod.yaml \
                                    exec canvas \
                                    python manage.py associate_files --pdf {chip_id} canvas {classification_ids_arg}'",
            shell=True,
        )

    context = {
        "reports": gather_reports(chipsample),
        "button": "true",
        "chipsample": chipsample,
    }
    return render(request, "canvas/partials/report_list.html", context=context)


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


def get_evidences(cnv, acmg_loss=acmg_loss):
    for section in acmg_loss:
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
    return acmg_loss


def get_acmg(request):
    if request.method == "POST":
        cnv_pk = request.POST.get("cnv_pk")
        cnv = CNV.objects.get(pk=cnv_pk)
        cnv.cnv_json["total_score"] = cnv.cnv_json.pop("Total score", None)
        acmg_loss = get_evidences(cnv)
    return render(
        request,
        "canvas/components/variant_modal.html",
        {"cnv": cnv, "acmg_loss": acmg_loss},
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
        rois = request.POST.get("rois", "[]")
        try:
            rois = json.loads(rois)  # Deserialize JSON string into a Python list
        except json.JSONDecodeError:
            rois = []
        context = {
            "chipsample": chipsample,
            "rois": rois,
            "showModal": True,
        }
    return render(request, "canvas/components/cnv_modal.html", context)
