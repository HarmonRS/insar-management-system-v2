PRO Run_Batch_Task_Import

  ;===========================================================================
  ;                        --- 用户配置区域 ---
  ; 1. **数据根目录 (rootDir)**
  ; 指定包含所有Task_开头文件夹的根目录路径。
  ; !!! 重要: 路径末尾不要带斜杠，脚本会自动添加。
  ; - 正确示例: 'C:\MyData\LuTan1' 或 '\\SERVER\Share\Data'

  ; 2. **处理数量 (numToProcess)**
  ; 指定要处理的Task文件夹的最大数量。
  ; - 如果设置为 0 或一个负数, 程序将处理所有找到的文件夹。

  ;===========================================================================
  temporary_directory = 'D:\Sarscape_IDL_Area'
  SARscape_Batch_Init,Temp_Directory=temporary_directory
  rootDir = '\\DESKTOP-N16HJ84\InSAR_Storage_1\Raw_Image_Pool_2025_2'
  numToProcess = 0
  ;===========================================================================
  ;                        --- 脚本执行区域 ---
  ;===========================================================================
  pathSep = ''
  IF (!VERSION.OS_FAMILY EQ 'Windows') THEN pathSep = '\' ELSE pathSep = '/'
  e = ENVI(/HEADLESS)

  IF rootDir EQ '' OR FILE_TEST(rootDir, /DIRECTORY) EQ 0 THEN BEGIN
    PRINT, '错误: "rootDir" 参数未设置或指定的目录不存在: ' + rootDir
    RETURN
  ENDIF

  PRINT, '=============================================='
  PRINT, '开始批量导入陆探一号数据...'
  PRINT, '扫描目录: ' + rootDir
  PRINT, '=============================================='

  ; 查找所有Task_开头的文件夹
  PRINT, '正在扫描Task_开头的文件夹...'
  taskFolders = FILE_SEARCH(rootDir + pathSep + 'Task_*', /FOLDERS, COUNT=nTasks)

  IF nTasks EQ 0 THEN BEGIN
    PRINT, '在指定目录下没有找到任何Task_开头的文件夹。'
    RETURN
  ENDIF

  PRINT, '共找到 ' + STRING(nTasks) + ' 个Task文件夹，准备开始处理...'
  PRINT, ''

  processed_count = 0

  FOREACH taskFolder, taskFolders DO BEGIN
    IF (numToProcess GT 0) AND (processed_count GE numToProcess) THEN BEGIN
      PRINT, ''
      PRINT, '已达到指定的处理数量 (' + STRING(numToProcess) + ')，程序终止。'
      BREAK
    ENDIF

    taskName = FILE_BASENAME(taskFolder)
    PRINT, '=== 正在处理任务文件夹: ' + taskName + ' ==='

    ; 检查master和slave子文件夹
    masterDir = taskFolder + pathSep + 'master'
    slaveDir = taskFolder + pathSep + 'slave'

    ; 处理master文件夹
    IF FILE_TEST(masterDir, /DIRECTORY) THEN BEGIN
      PRINT, '--- 处理master文件夹 ---'
      processed_count = processed_count + Process_LuTan_Folder(masterDir, pathSep)
    ENDIF ELSE BEGIN
      PRINT, '警告: ' + taskName + ' 中未找到master文件夹'
    ENDELSE

    ; 处理slave文件夹
    IF FILE_TEST(slaveDir, /DIRECTORY) THEN BEGIN
      PRINT, '--- 处理slave文件夹 ---'
      processed_count = processed_count + Process_LuTan_Folder(slaveDir, pathSep)
    ENDIF ELSE BEGIN
      PRINT, '警告: ' + taskName + ' 中未找到slave文件夹'
    ENDELSE

    PRINT, '完成处理任务文件夹: ' + taskName
    PRINT, ''
  ENDFOREACH

  PRINT, '=============================================='
  PRINT, '所有任务已完成！'
  PRINT, '总共成功处理了 ' + STRING(processed_count) + ' 个数据文件夹。'
  PRINT, '=============================================='

END

; 处理单个陆探一号数据文件夹的子函数
FUNCTION Process_LuTan_Folder, folderPath, pathSep
  COMPILE_OPT STRICTARR

  baseName = FILE_BASENAME(folderPath)
  PRINT, '-> 正在检查文件夹: ' + baseName

  ; 直接使用master/slave文件夹作为输出目录，不再创建envi_import子目录
  outputDir = folderPath

  ; 检查是否已经处理过（通过检查是否存在.sml文件）
  check_sml_file = FILE_SEARCH(outputDir + pathSep + '*.sml', COUNT=sml_count)
  IF sml_count GT 0 THEN BEGIN
    PRINT, '-> 跳过: ' + baseName + ' 文件夹中已存在处理结果。'
    RETURN, 0
  ENDIF

  ; 查找元数据文件 - 查找文件夹中所有的.meta.xml文件
  metaFiles = FILE_SEARCH(folderPath + pathSep + '*.meta.xml', COUNT=nMetaFiles)

  IF nMetaFiles EQ 0 THEN BEGIN
    PRINT, '-> 警告: 在文件夹 ' + baseName + ' 中未找到元数据文件(*.meta.xml)，跳过。'
    RETURN, 0
  ENDIF

  ; 处理找到的元数据文件
  success_count = 0
  FOR i = 0, nMetaFiles - 1 DO BEGIN
    inputFile = metaFiles[i]
    metaFileName = FILE_BASENAME(inputFile)
    PRINT, '-> 准备导入: ' + metaFileName

    ; 执行导入任务
    oSB = obj_new('SARscapeBatch')
    task = ENVITask('SARsImportLuTan1')
    task.INPUT_FILE_LIST = [inputFile]
    task.ROOT_URI_FOR_OUTPUT = outputDir  ; 直接输出到master/slave文件夹

    CATCH, error_status
    IF error_status NE 0 THEN BEGIN
      PRINT, '-> 错误: 在处理 ' + metaFileName + ' 时发生严重错误。'
      PRINT, !ERROR_STATE.MSG
      CATCH, /CANCEL
      CONTINUE
    ENDIF

    task.Execute
    CATCH, /CANCEL
    PRINT, '-> 成功: 数据已导入到 ' + baseName + ' 文件夹中。'
    success_count = success_count + 1
  ENDFOR

  RETURN, success_count
END