/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./templates/**/*.html",
        "./apps/**/templates/**/*.html",
        "./static/js/**/*.js",
        "!./static/js/**/*.min.js",
    ],
    theme: {
        extend: {
            colors: {
                kec: {
                    red: '#134E40', // Legacy primary class name
                    green: '#134E40',
                    forest: '#0B3028',
                    gold: '#D5B46C',
                    ivory: '#F6F5EF',
                    line: '#DDDFD7',
                    danger: '#B42332',
                    white: '#FFFFFF',
                    charcoal: '#183229',
                    grey: '#66726A', // Only for secondary text
                }
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', 'sans-serif'],
            },
            borderRadius: {
                'kec': '12px',
            }
        },
    },
    plugins: [],
}
