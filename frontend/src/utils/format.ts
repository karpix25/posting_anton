
export function formatCron(cron: string): string {
    if (!cron) return 'Отключено';
    const parts = cron.split(' ');
    if (parts.length < 5) return cron;
    const min = parts[0] ? parts[0].padStart(2, '0') : '00';
    const hour = parts[1] ? parts[1].padStart(2, '0') : '00';
    return `${hour}:${min} (Ежедневно)`;
}

export function formatDate(isoStr: string): string {
    if (!isoStr) return '-';
    // Return relative or simplified date
    return new Date(isoStr).toLocaleString('ru-RU');
}
