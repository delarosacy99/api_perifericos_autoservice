from rest_framework import serializers

from .models import AcceptedBanknote, CashCollectionSession


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown_fields = set(data) - set(self.fields)
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["Campo no permitido."] for field in sorted(unknown_fields)}
            )
        return super().to_internal_value(data)


class AcceptedBanknoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcceptedBanknote
        fields = [
            "id",
            "device_event_id",
            "channel",
            "currency",
            "denomination_minor",
            "accepted_at",
        ]


class CashCollectionSerializer(serializers.ModelSerializer):
    accepted_banknotes = AcceptedBanknoteSerializer(many=True, read_only=True)

    class Meta:
        model = CashCollectionSession
        fields = [
            "id",
            "idempotency_key",
            "remote_session_id",
            "currency",
            "accepted_amount_minor",
            "status",
            "metadata",
            "created_at",
            "updated_at",
            "started_at",
            "stopped_at",
            "stop_reason",
            "accepted_banknotes",
        ]


class CreateCashCollectionSerializer(StrictSerializer):
    idempotency_key = serializers.CharField(max_length=100)
    remote_session_id = serializers.CharField(max_length=128)
    currency = serializers.CharField(min_length=3, max_length=3)
    metadata = serializers.JSONField(required=False)

    def validate_currency(self, value: str) -> str:
        if not value.isalpha():
            raise serializers.ValidationError(
                "La moneda debe ser un código alfabético de 3 letras."
            )
        return value.upper()


class StopCashCollectionSerializer(StrictSerializer):
    reason = serializers.ChoiceField(choices=CashCollectionSession.StopReason.choices)


class SimulateNoteSerializer(StrictSerializer):
    collection_session_id = serializers.UUIDField()
    denomination_minor = serializers.IntegerField(min_value=1)
