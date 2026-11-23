(() => {
    const summary = document.querySelector('[data-error-summary]');
    if (summary) summary.focus();
    document.querySelectorAll('.field-errors').forEach(errors => {
        const wrapper = errors.closest('.form-field,.checkbox-field');
        if (!wrapper) return;
        const field = wrapper.querySelector('input,select,textarea');
        if (field) { field.setAttribute('aria-invalid', 'true'); if (errors.id) field.setAttribute('aria-describedby', errors.id); }
    });
    document.querySelectorAll('form').forEach(form => {
        const unit = form.querySelector('select[name="unit"],select[name="department"]');
        const other = form.querySelector('[data-other-unit-field]');
        if (unit && other) {
            const update = () => {
                other.hidden = unit.value !== 'other';
                const input = other.querySelector('input');
                if (input) input.required = unit.value === 'other';
            };
            unit.addEventListener('change', update);
            update();
        }
    });
})();
