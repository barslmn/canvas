import csv
import json
import os
import secrets
import socket
import struct
import subprocess
import tempfile
import zipfile
from io import BytesIO

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django_htmx.http import retarget

from canvas.acmg import acmg_gain, acmg_loss
from canvas.models import (
    CNV,
    IDAT,
    Chip,
    ChipSample,
    ChipType,
    Classification,
    Institution,
    Report,
    Sample,
    SampleType,
)
from canvas.read_tsv import read_sample_from_tsv


def get_safe_token(length=6):
    """Generate a command-line safe token using only alphanumeric characters."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
    with open(settings.BASE_DIR.joinpath(".git/FETCH_HEAD")) as f:
        return f.read().splitlines()[0][:6]


def start_run(chip_id):
    if not settings.DEBUG:
        HOST_IP = get_default_gateway_linux()
        MINIO_IP = socket.gethostbyname("minio")
        label = get_safe_token(6)
        chip_type = Chip.objects.get(chip_id=chip_id).chip_type

        # Create the sample sheet file
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as ss:
            ss.write(f"sample_id\tprotocol_id\tinstitution\n")
            for cs in ChipSample.objects.filter(chip__chip_id=chip_id):
                ss.write(
                    f"{chip_id}_{cs.position}\t{cs.sample.protocol_id}\t{cs.sample.institution.name}\n"
                )
            ss.flush()
            subprocess.run(
                f"scp {ss.name} canvas@{HOST_IP}:/tmp",
                shell=True,
            )

        # Create the Nextflow configuration file
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as nfc:
            nfc.write(
                f"""aws {{
  access_key = "{settings.MINIO_STORAGE_ACCESS_KEY}"
  secret_key = "{settings.MINIO_STORAGE_SECRET_KEY}"
  client {{
    endpoint = 'https://minio.cnvcanvas.com'
    s3PathStyleAccess = true
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
                f"scp {nfc.name} canvas@{HOST_IP}:/tmp",
                shell=True,
            )

        # Create the script to execute on the host
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as script:
            script.write(
                f"""#!/bin/bash
export TS_SOCKET="/home/canvas/ts/ts_start_run.socket"
export NXF_WORK="/home/canvas/work"
job_dir="/home/canvas/jobs/{label}"
mkdir -p "$job_dir"
cd "$job_dir"
tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \\
    --chip_id {chip_id} \\
    --bpm s3://canvas/{chip_type.bpm.name} \\
    --csv s3://canvas/{chip_type.csv.name} \\
    --egt s3://canvas/{chip_type.egt.name} \\
    --fasta s3://canvas/{chip_type.genome.fasta.name} \\
    --pfb s3://canvas/{chip_type.pfb.name} \\
    --band s3://canvas/{chip_type.genome.band.name} \\
    --tex_template /home/canvas/canvas-pipeline/template/base_template.tex \\
    --samplesheet {ss.name} \\
    -c {nfc.name} \\
    -with-report {chip_id}_{label}.html \\
    -profile docker

tsp -D $(tsp -l | grep {label} | cut -d' ' -f1) \\
                bash -c 'CONTAINER_ID=$(docker ps -q -f name=canvas_canvas.1) &&
                docker exec $CONTAINER_ID python manage.py associate_files \\
                {chip_id} canvas'
"""
            )
            script.flush()
            subprocess.run(
                f"scp {script.name} canvas@{HOST_IP}:/tmp",
                shell=True,
            )

        # Execute the script on the host
        subprocess.run(
            f"ssh canvas@{HOST_IP} 'chmod +x {script.name} && {script.name}'",
            shell=True,
        )


def get_samples_for_user(user, samples):
    user_groups = user.groups.all()
    if user.is_staff:
        samples = samples
    else:
        samples = samples.filter(institution__group__in=user_groups)
    return samples


def get_chips_for_user(user, chips):
    user_groups = user.groups.all()
    if user.is_staff:
        chips = chips
    else:
        chips = chips.filter(
            chipsample__sample__institution__group__in=user_groups
        ).distinct()
    return chips


def get_institutions_for_user(user, institutions):
    user_groups = user.groups.all()
    if user.is_staff:
        institutions = institutions
    else:
        institutions = institutions.filter(group__in=user_groups)
    return institutions


def index(request):
    samples = get_samples_for_user(request.user, samples=Sample.objects.all()).order_by(
        "-entry_date"
    )
    len_samples = len(samples)
    sample_paginator = Paginator(samples, 12)
    samples = sample_paginator.get_page(1)

    chips = get_chips_for_user(request.user, chips=Chip.objects.all()).order_by(
        "-entry_date"
    )
    len_chips = len(chips)
    chip_paginator = Paginator(chips, 12)
    chips = chip_paginator.get_page(1)

    label = get_safe_token(6)
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
        if "Total score" in cnv_json.keys():
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
    user_cnv_pos_bedgraphs = []
    user_cnv_neg_bedgraphs = []

    for bedgraph in bedgraphs:
        if bedgraph.bedgraph_type == "LRR":
            lrr_bedgraph = bedgraph
        elif bedgraph.bedgraph_type == "BAF":
            baf_bedgraph = bedgraph
        elif bedgraph.bedgraph_type == "CNV_pos":
            if len(bedgraph.bedgraph.name.split("_")) == 4:
                cnv_pos_bedgraph = bedgraph
            elif len(bedgraph.bedgraph.name.split("_")) == 5:
                user_cnv_pos_bedgraphs.append(bedgraph)
        elif bedgraph.bedgraph_type == "CNV_neg":
            if len(bedgraph.bedgraph.name.split("_")) == 4:
                cnv_neg_bedgraph = bedgraph
            elif len(bedgraph.bedgraph.name.split("_")) == 5:
                user_cnv_neg_bedgraphs.append(bedgraph)
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
            "user_cnv_pos_bedgraphs": user_cnv_pos_bedgraphs,
            "user_cnv_neg_bedgraphs": user_cnv_neg_bedgraphs,
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
        label = get_safe_token(6)

        # Create temporary files
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as cnv_file:
            json.dump(cnvs, cnv_file)
            cnv_file.flush()
            subprocess.run(f"scp {cnv_file.name} canvas@{HOST_IP}:/tmp", shell=True)

        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as nfc:
            nfc.write(
                f"""aws {{
  access_key = "{settings.MINIO_STORAGE_ACCESS_KEY}"
  secret_key = "{settings.MINIO_STORAGE_SECRET_KEY}"
  client {{
    endpoint = 'https://minio.cnvcanvas.com'
    s3PathStyleAccess = true
  }}
}}
profiles {{
  docker {{
    docker.enabled = true
  }}
}}"""
            )
            nfc.flush()
            subprocess.run(f"scp {nfc.name} canvas@{HOST_IP}:/tmp", shell=True)

        # Create the script file
        with tempfile.NamedTemporaryFile(delete_on_close=False, mode="w") as script:
            script.write(
                f"""#!/bin/bash
export TS_SOCKET="/home/canvas/ts/ts_create_report.socket"
job_dir="/home/canvas/jobs/{label}"
mkdir -p "$job_dir"
cd "$job_dir"
tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \\
    --chip_id "{chip_id}" \\
    --chip_type "{chip_type}" \\
    --position "{chipsample.position}" \\
    --tex_template /home/canvas/canvas-pipeline/template/base_template.tex \\
    --cnvs "{cnv_file.name}" \\
    --institute "{chipsample.sample.institution.name}" \\
    --protocol_id "{chipsample.sample.protocol_id}" \\
    --version {version} \\
    -c {nfc.name} \\
    -with-report {chip_id}_{label}.html \\
    -profile docker


tsp -f -D $(tsp -l | grep {label} | cut -d" " -f1) \\
                bash -c 'CONTAINER_ID=$(docker ps -q -f name=canvas_canvas.1) &&
                docker exec $CONTAINER_ID python manage.py associate_files \\
                --pdf {chip_id} canvas \\
                {"--classification_ids " + " ".join(map(str, classification_ids)) if classification_ids else ""}'
"""
            )
            script.flush()
            subprocess.run(f"scp {script.name} canvas@{HOST_IP}:/tmp", shell=True)

        # Execute the script on the host
        subprocess.run(
            f"ssh canvas@{HOST_IP} 'chmod +x {script.name} && {script.name}'",
            shell=True,
        )

    context = {
        "reports": gather_reports(chipsample),
        "button": "true",
        "chipsample": chipsample,
    }
    response = render(
        request, "canvas/components/create_report_button.html", context=context
    )
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
            chip_id = chipsample.chip.chip_id
            cnv = CNV.objects.create(
                chipsample=chipsample,
                user=request.user,
                cnv_json={"user_cnv": roi, "user_copy_number": cn},
            )

            if not settings.DEBUG:
                HOST_IP = get_default_gateway_linux()
                MINIO_IP = socket.gethostbyname("minio")
                label = get_safe_token(6)

                # Write CNV data to temporary file
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as cnv_file:
                    cnv_file.write(f"{chromosome}\t{start}\t{end}\t{cn}\n")
                    cnv_file.flush()
                    subprocess.run(
                        f"scp {cnv_file.name} canvas@{HOST_IP}:/tmp",
                        shell=True,
                    )

                # Create Nextflow config file
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as nfc:
                    nfc.write(
                        f"""aws {{
  access_key = "{settings.MINIO_STORAGE_ACCESS_KEY}"
  secret_key = "{settings.MINIO_STORAGE_SECRET_KEY}"
  client {{
    endpoint = 'https://minio.cnvcanvas.com'
    s3PathStyleAccess = true
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
                        f"scp {nfc.name} canvas@{HOST_IP}:/tmp",
                        shell=True,
                    )

                with tempfile.NamedTemporaryFile(delete=False, mode="w") as script:
                    script.write(
                        f"""#!/bin/bash
export TS_SOCKET="/home/canvas/ts/ts_cnv_edit.socket"
job_dir="/home/canvas/jobs/{label}"
mkdir -p "$job_dir"
cd "$job_dir"
tsp -L {label} nextflow /home/canvas/canvas-pipeline/main.nf \\
    --chip_id "{chip_id}" \\
    --position "{chipsample.position}" \\
    --cnv_bed "{cnv_file.name}" \\
    --snap_probes {snap_probes} \\
    --cnv_pk {cnv.pk} \\
    --band s3://canvas/{chipsample.chip.chip_type.genome.band.name} \\
    -c {nfc.name} \\
    -profile docker

tsp -f -D $(tsp -l | grep {label} | cut -d" " -f1) \\
                        bash -c 'CONTAINER_ID=$(docker ps -q -f name=canvas_canvas.1) &&
                        docker exec $CONTAINER_ID \\
                        python manage.py associate_files \\
                        --cnv_pk {cnv.pk} {chip_id} canvas'
"""
                    )
                    script.flush()
                    subprocess.run(
                        f"scp {script.name} canvas@{HOST_IP}:/tmp",
                        shell=True,
                    )

                # Transfer and execute script
                subprocess.run(
                    f"ssh canvas@{HOST_IP} 'chmod +x {script.name} && {script.name}'",
                    shell=True,
                )

            cnv = CNV.objects.get(pk=cnv.pk)
            cnv_json = cnv.cnv_json
            cnv_json["cnv_pk"] = cnv.pk
            cnv_json["total_score"] = cnv_json.pop("Total score", None)
            cnv_json["addToReport"] = False

            return render(
                request,
                "canvas/partials/cnv_edit_success.html",
                {
                    "success": True,
                    "message": "CNV successfully added",
                    "cnv_json": json.dumps(cnv_json),
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
