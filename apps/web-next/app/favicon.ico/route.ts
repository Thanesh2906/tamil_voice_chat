/** Handle browsers that still request the conventional favicon URL. */
export function GET() {
  return new Response(null, {
    status: 307,
    headers: { Location: "/icon.svg", "Cache-Control": "public, max-age=86400" },
  });
}
