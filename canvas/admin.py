from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.utils.html import format_html

# Register your models here.
from .models import (
    Lot,
    Genome,
    ChipType,
    Chip,
    SampleType,
    Institution,
    Sample,
    ChipSample,
    IDAT,
    GTC,
    VCF,
    BedGraph,
    CNV,
    Classification,
    Report,
    Note,
)


class GenomeAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


class ChipAdmin(admin.ModelAdmin):
    list_display = ["chip_id", "lot", "entry_date", "protocol_start_date", "scan_date"]
    search_fields = ["chip_id", "lot__lot_number"]


class ChipTypeAdmin(admin.ModelAdmin):
    list_display = [
        "name",
    ]


class SampleTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class InstitutionAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class SampleAdmin(admin.ModelAdmin):
    list_display = (
        "protocol_id",
        "entry_date",
        "arrival_date",
        "study_date",
        "concentration",
        "institution",
        "sample_type",
        "description",
    )
    autocomplete_fields = ["repeat"]
    search_fields = ("protocol_id", "institution__name")


class ChipSampleAdmin(admin.ModelAdmin):
    list_display = (
        "protocol_id",
        "chip",
        "position",
        "call_rate",
    )
    search_fields = (
        "sample__protocol_id",
        "chip__chip_id",
        "sample__institution__name",
    )
    autocomplete_fields = ["sample", "chip"]

    def protocol_id(self, obj):
        if obj.sample:
            return obj.sample.protocol_id
        else:
            return "No sample yet"


class IDATAdmin(admin.ModelAdmin):
    list_display = ["idat", "protocol_id"]
    search_fields = ["idat", "protocol_id"]

    def protocol_id(self, obj):
        if obj.chipsample:
            if obj.chipsample.sample:
                return obj.chipsample.sample.protocol_id
        else:
            return "none"


class GTCAdmin(admin.ModelAdmin):
    list_display = ["gtc", "protocol_id"]
    search_fields = ["gtc", "protocol_id"]

    def protocol_id(self, obj):
        return obj.chipsample.sample.protocol_id


class VCFAdmin(admin.ModelAdmin):
    list_display = ["vcf", "protocol_id"]
    search_fields = ["vcf", "protocol_id"]

    def protocol_id(self, obj):
        return obj.chipsample.sample.protocol_id


class BedGraphAdmin(admin.ModelAdmin):
    list_display = ["chipsample", "bedgraph", "bedgraph_type", "protocol_id"]
    search_fields = ["bedgraph", "chipsample__sample__protocol_id"]

    autocomplete_fields = ["chipsample"]

    def protocol_id(self, obj):
        try:
            return obj.chipsample.sample.protocol_id
        except:
            return "abc"


class CNVAdmin(admin.ModelAdmin):
    list_display = ["variant_id", "entry_date", "protocol_id"]
    search_fields = ["variant_id", "cnv_json"]

    autocomplete_fields = ["chipsample"]

    def protocol_id(self, obj):
        try:
            return obj.chipsample.sample.protocol_id
        except:
            return "abc"


class ClassificationAdmin(admin.ModelAdmin):
    list_display = ["cnv", "entry_date", "user"]


class ReportAdmin(admin.ModelAdmin):
    list_display = ["entry_date", "report"]
    autocomplete_fields = ["chipsample"]


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ['content_preview', 'user', 'content_type_str', 'object_link', 'created_at', 'updated_at']
    list_filter = ['content_type', 'user', 'created_at', 'updated_at']
    search_fields = ['content', 'user__username', 'object_id']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['user']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']

    def content_preview(self, obj):
        """Return a truncated version of the content for the list view."""
        return obj.content[:100] + '...' if len(obj.content) > 100 else obj.content
    content_preview.short_description = 'Content'

    def content_type_str(self, obj):
        """Return a human-readable content type."""
        return obj.content_type.model.title()
    content_type_str.short_description = 'Type'
    content_type_str.admin_order_field = 'content_type__model'

    def object_link(self, obj):
        """Return a link to the related object's admin page."""
        try:
            url = f'/admin/{obj.content_type.app_label}/{obj.content_type.model}/{obj.object_id}/change/'
            return format_html('<a href="{}">{}</a>', url, str(obj.content_object))
        except:
            return f'Object {obj.object_id}'
    object_link.short_description = 'Related Object'

    def get_queryset(self, request):
        """Optimize the queryset by prefetching related fields."""
        return super().get_queryset(request).select_related(
            'user',
            'content_type'
        )

    fieldsets = [
        (None, {
            'fields': ('content',)
        }),
        ('Metadata', {
            'fields': ('user', 'created_at', 'updated_at')
        }),
        ('Content Type Information', {
            'fields': ('content_type', 'object_id'),
            'classes': ('collapse',)
        })
    ]


admin.site.register(Lot)
admin.site.register(Genome, GenomeAdmin)
admin.site.register(Chip, ChipAdmin)
admin.site.register(ChipType, ChipTypeAdmin)
admin.site.register(SampleType, SampleTypeAdmin)
admin.site.register(Institution, InstitutionAdmin)
admin.site.register(Sample, SampleAdmin)
admin.site.register(ChipSample, ChipSampleAdmin)
admin.site.register(IDAT, IDATAdmin)
admin.site.register(GTC, GTCAdmin)
admin.site.register(VCF, VCFAdmin)
admin.site.register(BedGraph, BedGraphAdmin)
admin.site.register(CNV, CNVAdmin)
admin.site.register(Classification, ClassificationAdmin)
admin.site.register(Report, ReportAdmin)
