/**
 * Совместимость WP5 (стадия Integrate): локальный дубль контракта §3.9
 * заменён реэкспортом общего FileDropzone (WP4, shared/ui/file-dropzone) —
 * теперь с иллюстрацией 'import-drop' из общего реестра §4.
 */
export { FileDropzone, type FileDropzoneProps, type IllustrationName } from '@/shared/ui/file-dropzone';
