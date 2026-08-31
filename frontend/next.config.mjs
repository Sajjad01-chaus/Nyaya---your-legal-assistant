/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emits a self-contained server bundle so the runtime image does not need
  // node_modules. Keeps the image small.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
