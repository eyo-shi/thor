/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_THOR_S3_BUCKETS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
