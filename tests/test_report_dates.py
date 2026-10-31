"""Report date ranges are whole local days, and averages use what was actually paid."""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.expenses.models import Expense


def _expense(user, category, when, amount='100000', tax='19000'):
    return Expense.objects.create(
        user=user, category=category, description='Gasto', amount=Decimal(amount),
        tax_amount=Decimal(tax), currency='COP', incurred_at=when,
    )


class TestReportRange:
    def test_today_is_included_when_end_date_is_today(self, auth_client, user, system_categories):
        today = timezone.localdate()
        late_today = timezone.make_aware(datetime.combine(today, time(21, 30)))
        _expense(user, system_categories[0], late_today)
        start = (today - timedelta(days=30)).isoformat()
        response = auth_client.get(
            f'/api/v1/reports/dashboard/summary/?start_date={start}&end_date={today.isoformat()}'
        )
        assert response.json()['data']['expenses_count'] == 1

    def test_start_date_is_inclusive_from_midnight(self, auth_client, user, system_categories):
        today = timezone.localdate()
        early = timezone.make_aware(datetime.combine(today, time(0, 5)))
        _expense(user, system_categories[0], early)
        response = auth_client.get(
            f'/api/v1/reports/dashboard/monthly-expenses/?start_date={today}&end_date={today}'
        )
        assert sum(item['count'] for item in response.json()['data']) == 1

    def test_average_and_max_include_taxes(self, auth_client, user, system_categories):
        _expense(user, system_categories[0], timezone.now(), amount='100000', tax='19000')
        _expense(user, system_categories[0], timezone.now(), amount='50000', tax='0')
        data = auth_client.get('/api/v1/reports/dashboard/summary/?months=1').json()['data']
        assert Decimal(str(data['total_expenses'])) == Decimal('169000')
        assert Decimal(str(data['max_expense'])) == Decimal('119000')
        assert Decimal(str(data['average_expense'])) == Decimal('84500')
