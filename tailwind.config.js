/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./templates/**/*.html",
        "./apps/**/templates/**/*.html",
    ],
    theme: {
        extend: {
            colors: {
                kec: {
                    red: '#D1121C',
                    white: '#FFFFFF',
                    charcoal: '#1A1A1A',
                    grey: '#6B7280', // Only for secondary text
                }
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', 'sans-serif'],
                syncopate: ['Syncopate', 'system-ui', 'sans-serif']
            },
            borderRadius: {
                'kec': '6px',
            }
        },
    },
    plugins: [],
}