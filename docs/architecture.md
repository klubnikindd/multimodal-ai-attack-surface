# Архитектура проекта

```
graph TD
    CLI["CLI (attack_surface.cli)"] -->|"Запускает"| ExtractMetadata["extract_metadata" в модуле `metadata.py`]
    ExtractMetadata -->|"Для изображений"| ImageHandler["Image Handler (PIL, exifread)" ]
    ExtractMetadata -->|"Для видео"| VideoHandler["Video Handler (ffmpeg)" ]
    ImageHandler -->|"Возвращает"| JSON["JSON метаданные"]
    VideoHandler -->|"Возвращает"| JSON
    CLI -->|"Выводит"| JSON
```

Диаграмма описывает поток данных от командной строки к API извлечения метаданных, а затем к обработчикам изображений и видео, которые формируют итоговый JSON.
