;================================================================================
; ### HELPER FUNCTION: READ SML PARAMETER ###
;================================================================================
FUNCTION Read_SML_Parameter, sml_file, param_name_to_find
  IF N_ELEMENTS(sml_file) EQ 0 OR sml_file EQ '' THEN BEGIN
    PRINT, 'Error: An empty or undefined filename was passed to Read_SML_Parameter.'
    RETURN, ''
  ENDIF
  IF NOT FILE_TEST(sml_file) THEN BEGIN
    PRINT, 'Error: SML file not found -> ' + sml_file
    RETURN, ''
  ENDIF
  lun = -1
  ON_ERROR, 2
  OPENR, lun, sml_file, /GET_LUN, ERROR=err
  IF (err NE 0) THEN BEGIN
    PRINT, 'Error: Could not open SML file for reading -> ' + sml_file
    IF lun NE -1 THEN FREE_LUN, lun
    RETURN, ''
  ENDIF
  found_value = ''
  line = ''
  start_tag = '<' + STRUPCASE(param_name_to_find) + '>'
  end_tag = '</' + STRUPCASE(param_name_to_find) + '>'
  WHILE NOT EOF(lun) DO BEGIN
    READF, lun, line
    clean_line = STRTRIM(line, 2)
    line_upper = STRUPCASE(clean_line)
    start_pos = STRPOS(line_upper, start_tag)
    IF start_pos NE -1 THEN BEGIN
      end_pos = STRPOS(line_upper, end_tag, start_pos)
      IF end_pos NE -1 THEN BEGIN
        start_extract = start_pos + STRLEN(start_tag)
        value_length = end_pos - start_extract
        found_value = STRMID(clean_line, start_extract, value_length)
        BREAK
      ENDIF
    ENDIF
  ENDWHILE
  IF lun NE -1 THEN FREE_LUN, lun
  RETURN, found_value
END

;================================================================================
; ### HELPER FUNCTION: CREATE GCPs FROM COHERENCE ###
;================================================================================
FUNCTION CREATE_GCPS_FROM_COHERENCE, coherence_file, output_shp_file, COH_THRESHOLD=coh_threshold, NUM_POINTS=num_points

  ON_ERROR, 2
  IF FILE_TEST(output_shp_file) THEN FILE_DELETE, output_shp_file, /QUIET

  IF N_ELEMENTS(coh_threshold) EQ 0 THEN coh_threshold = 0.7
  IF N_ELEMENTS(num_points) EQ 0 THEN num_points = 100

  e = ENVI(/HEADLESS)

  PRINT, 'Opening coherence file: ' + coherence_file
  oRaster = e.OpenRaster(coherence_file)

  ns = oRaster.NCOLUMNS
  nl = oRaster.NROWS
  data = oRaster.GetData(BANDS=[0])

  PRINT, 'Searching for GCPs with coherence > ' + STRTRIM(coh_threshold, 2)

  grid_dim = CEIL(SQRT(num_points))
  x_step = FLOOR(ns / grid_dim)
  y_step = FLOOR(nl / grid_dim)

  pixel_points_x = [] & pixel_points_y = []

  FOR j=0L, grid_dim-1 DO BEGIN
    FOR i=0L, grid_dim-1 DO BEGIN
      x_start = i * x_step & y_start = j * y_step
      x_end = (i+1) * x_step -1 < (ns-1)
      y_end = (j+1) * y_step -1 < (nl-1)
      IF (x_start GE ns) OR (y_start GE nl) THEN CONTINUE
      cell_data = data[x_start:x_end, y_start:y_end]
      max_val = MAX(cell_data, max_idx)
      IF max_val GE coh_threshold THEN BEGIN
        max_y_cell = max_idx / (x_end - x_start + 1)
        max_x_cell = max_idx MOD (x_end - x_start + 1)
        pixel_points_x = [pixel_points_x, x_start + max_x_cell]
        pixel_points_y = [pixel_points_y, y_start + max_y_cell]
      ENDIF
    ENDFOR
  ENDFOR

  point_count = N_ELEMENTS(pixel_points_x)
  IF point_count EQ 0 THEN BEGIN
    PRINT, 'Error: No GCPs found.' & OBJ_DESTROY, oRaster & RETURN, 0
  ENDIF

  PRINT, 'Found ' + STRTRIM(point_count, 2) + ' suitable GCPs.'
  PRINT, 'Creating shapefile using final authoritative methodology...'

  oShape = OBJ_NEW('IDLffShape', output_shp_file, ENTITY_TYPE=1, /UPDATE)

  oShape->AddAttribute, 'SHP_ID', 3, 10
  oShape->AddAttribute, 'GCP_LABEL', 7, 20
  oShape->AddAttribute, 'GCP_TYPE', 7, 20
  oShape->AddAttribute, 'GCP_COLUMN', 5, 24, PRECISION=6
  oShape->AddAttribute, 'GCP_ROW', 5, 24, PRECISION=6
  oShape->AddAttribute, 'GCP_OTHER_', 7, 10 ; User-verified field name

  attr_template = oShape->GetAttributes(/ATTRIBUTE_STRUCTURE)

  entNew = {IDL_SHAPE_ENTITY}
  entNew.SHAPE_TYPE = 1

  FOR i=0, point_count-1 DO BEGIN
    x_float = FLOAT(pixel_points_x[i])
    y_float = FLOAT(pixel_points_y[i])

    entNew.ISHAPE = i
    entNew.BOUNDS = [x_float, y_float, 0.0, 0.0, x_float, y_float, 0.0, 0.0]
    oShape->PutEntity, entNew

    attr = attr_template
    attr.ATTRIBUTE_0 = i
    attr.ATTRIBUTE_1 = 'GCP_' + STRTRIM(i+1, 2)
    attr.ATTRIBUTE_2 = 'undefined'
    attr.ATTRIBUTE_3 = x_float
    attr.ATTRIBUTE_4 = y_float
    attr.ATTRIBUTE_5 = ''
    oShape->SetAttributes, i, attr
  ENDFOR

  oShape->Close
  OBJ_DESTROY, [oShape, oRaster]

  PRINT, 'Successfully created GCP shapefile: ' + output_shp_file
  RETURN, 1
END

;================================================================================
; ### 执行单个D-InSAR工作流的函数 ###
;================================================================================
FUNCTION Execute_Single_Dinsar_Workflow, master_base_file, slave_base_file, dem_base_file, workflow_root_name, $
  target_ground_resolution_m, filter_method, unwrapping_coh_threshold, $
  gcp_coh_threshold, gcp_number, geocoding_coh_threshold, geocoding_pixel_size_m

  ; ===================================================================
  ; ### STEP 1: INTERFEROGRAM GENERATION ###
  ; ===================================================================

  PRINT, '======================================================'
  PRINT, '### STEP 1: INTERFEROGRAM GENERATION ###'
  PRINT, '======================================================'

  master_sml = master_base_file + '.sml'
  slave_sml = slave_base_file + '.sml'

  m_rg_sp_str = Read_SML_Parameter(master_sml, 'PixelSpacingRg')
  m_az_sp_str = Read_SML_Parameter(master_sml, 'PixelSpacingAz')
  m_inc_ang_str = Read_SML_Parameter(master_sml, 'IncidenceAngle')
  s_rg_sp_str = Read_SML_Parameter(slave_sml, 'PixelSpacingRg')
  s_az_sp_str = Read_SML_Parameter(slave_sml, 'PixelSpacingAz')
  s_inc_ang_str = Read_SML_Parameter(slave_sml, 'IncidenceAngle')

  IF m_rg_sp_str EQ '' OR s_rg_sp_str EQ '' THEN RETURN, 0

  m_rg_sp = FLOAT(m_rg_sp_str) & m_az_sp = FLOAT(m_az_sp_str) & m_inc_ang = FLOAT(m_inc_ang_str)
  s_rg_sp = FLOAT(s_rg_sp_str) & s_az_sp = FLOAT(s_az_sp_str) & s_inc_ang = FLOAT(s_inc_ang_str)

  avg_az_spacing = (m_az_sp + s_az_sp) / 2.0
  azimuth_looks = (LONG(target_ground_resolution_m / avg_az_spacing)) > 1
  m_ground_rg_sp = m_rg_sp / SIN(m_inc_ang * !DPI / 180.0)
  s_ground_rg_sp = s_rg_sp / SIN(s_inc_ang * !DPI / 180.0)
  avg_ground_rg_sp = (m_ground_rg_sp + s_ground_rg_sp) / 2.0
  range_looks = (LONG(target_ground_resolution_m / avg_ground_rg_sp)) > 1

  task1_obj = OBJ_NEW('SARscapeBatch', Module='InSARInterferogramGeneration')
  prefix_1 = 'MAIN_INSAR_INTERFEROGRAM_GENERATION_CMD.'
  task1_obj->SetParam, prefix_1 + 'INPUT_REFERENCE_FILE_NAME', master_base_file
  task1_obj->SetParam, prefix_1 + 'INPUT_SECONDARY_FILE_NAME', slave_base_file
  task1_obj->SetParam, prefix_1 + 'DEM_FILE_NAME', dem_base_file
  task1_obj->SetParam, prefix_1 + 'OUTPUT_ROOT_FILE_NAME', workflow_root_name
  task1_obj->SetParam, prefix_1 + 'RG_LOOKS_NBR', STRING(range_looks)
  task1_obj->SetParam, prefix_1 + 'AZ_LOOKS_NBR', STRING(azimuth_looks)
  task1_obj->SetParam, 'COREGISTRATION_CMD.COREGISTRATION_WITH_DEM_FLAG', 'OK'
  task1_obj->SetParam, 'FLAT_CMD.MAKE_FLATTENING_FLAG', 'OK'
  ok_step1 = task1_obj->Execute()
  OBJ_DESTROY, task1_obj
  IF NOT ok_step1 THEN RETURN, 0
  PRINT, '### STEP 1: SUCCESS!'

  ; ===================================================================
  ; ### STEP 2: FILTERING AND COHERENCE GENERATION ###
  ; ===================================================================

  PRINT, '======================================================'
  PRINT, '### STEP 2: FILTERING AND COHERENCE GENERATION ###'
  PRINT, '======================================================'

  task2_obj = OBJ_NEW('SARscapeBatch', Module='InSARFilterAndCoherence')
  prefix_main2 = 'MAIN_INSAR_FILTER_COHERENCE_CMD.'
  prefix_filt2 = 'FILTERING_CMD.'
  task2_obj->SetParam, prefix_main2 + 'INPUT_INTERF_FILE_NAME', workflow_root_name + '_dint'
  task2_obj->SetParam, prefix_main2 + 'INPUT_REFERENCE_FILE_NAME', workflow_root_name + '_reference_pwr'
  task2_obj->SetParam, prefix_main2 + 'INPUT_SECONDARY_FILE_NAME', workflow_root_name + '_secondary_pwr'
  task2_obj->SetParam, prefix_main2 + 'OUT_ROOT_FILE', workflow_root_name
  task2_obj->SetParam, prefix_filt2 + 'FILTERING_METHOD', filter_method
  task2_obj->SetParam, prefix_main2 + 'COHERENCE_FLAG', 'OK'
  task2_obj->SetParam, prefix_main2 + 'INTERF_FILT_FLAG', 'OK'
  ok_step2 = task2_obj->Execute()
  OBJ_DESTROY, task2_obj
  IF NOT ok_step2 THEN RETURN, 0
  PRINT, '### STEP 2: SUCCESS!'

  ; ===================================================================
  ; ### STEP 3: ORBITAL TREND REMOVAL ###
  ; ===================================================================

  PRINT, '======================================================'
  PRINT, '### STEP 3: ORBITAL TREND REMOVAL ###'
  PRINT, '======================================================'

  reflat_output_root = workflow_root_name + '_rrpf'
  task3_obj = OBJ_NEW('SARscapeBatch', Module='InSARRemoveResidualPhaseFrequency')
  prefix_3 = 'MAIN_INSAR_REMOVE_RESIDUAL_PHASE_FREQUENCY.'
  task3_obj->SetParam, prefix_3 + 'INTERF_FILE_NAME', workflow_root_name + '_fint'
  task3_obj->SetParam, prefix_3 + 'COHERENCE_FILE_NAME', workflow_root_name + '_cc'
  task3_obj->SetParam, prefix_3 + 'OUT_ROOT_FILE_NAME', reflat_output_root
  ok_step3 = task3_obj->Execute()
  OBJ_DESTROY, task3_obj
  IF NOT ok_step3 THEN RETURN, 0
  PRINT, '### STEP 3: SUCCESS!'

  ; ===================================================================
  ; ### STEP 4: PHASE UNWRAPPING ###
  ; ===================================================================

  PRINT, '======================================================'
  PRINT, '### STEP 4: PHASE UNWRAPPING ###'
  PRINT, '======================================================'

  reflat_upha_output_file = workflow_root_name + '_upha'
  task4_obj = OBJ_NEW('SARscapeBatch', Module='InSARPhaseUnwrapping')
  prefix_main4 = 'MAIN_INSAR_PHASE_UNWRAPPING_CMD.'
  prefix_upha4 = 'UPHA_CMD.'
  task4_obj->SetParam, prefix_main4 + 'INFILE_NAME', reflat_output_root + '_fint'
  task4_obj->SetParam, prefix_main4 + 'COHERENCEFILE_NAME', workflow_root_name + '_cc'
  task4_obj->SetParam, prefix_upha4 + 'UPHA_COH_THRESHOLD', STRING(unwrapping_coh_threshold)
  task4_obj->SetParam, prefix_main4 + 'OUTFILE_NAME', reflat_upha_output_file
  ok_step4 = task4_obj->Execute()
  OBJ_DESTROY, task4_obj
  IF NOT ok_step4 THEN RETURN, 0
  PRINT, '### STEP 4: SUCCESS!'

  ; ===================================================================
  ; ### STEP 5: REFINEMENT AND REFLATTENING ###
  ; ===================================================================

  ; --- STEP 5A: Automatic GCP Generation ---
  PRINT, '======================================================'
  PRINT, '### STEP 5A: 正在自动生成GCP... ###'
  PRINT, '======================================================'

  auto_gcp_shp = workflow_root_name + '_auto_gcp.shp'
  gcp_success = CREATE_GCPS_FROM_COHERENCE(workflow_root_name + '_cc', auto_gcp_shp, COH_THRESHOLD=gcp_coh_threshold, NUM_POINTS=gcp_number)

  IF gcp_success EQ 0 THEN BEGIN
    PRINT, '错误: 自动生成GCP shapefile失败。流程终止。'
    RETURN, 0
  ENDIF
  PRINT, '### STEP 5A: SUCCESS!'

  ; --- STEP 5B: Refinement and Reflattening Execution ---
  PRINT, '======================================================'
  PRINT, '### STEP 5B: 正在执行 REFINEMENT AND REFLATTENING ###'
  PRINT, '======================================================'

  ref_output_root = workflow_root_name + '_reflat'
  task_refine = OBJ_NEW('SARscapeBatch', Module='InSARRefinementAndReflattening')
  prefix_refine = 'MAIN_INSAR_REFINEMENT_AND_REFLATTENING_CMD.'

  task_refine->SetParam, prefix_refine + 'INPUT_UPHA_FILE_NAME', reflat_upha_output_file
  task_refine->SetParam, prefix_refine + 'INPUT_REFERENCE_FILE_NAME', workflow_root_name + '_reference_pwr'
  task_refine->SetParam, prefix_refine + 'INPUT_SECONDARY_FILE_NAME', workflow_root_name + '_secondary_pwr'
  task_refine->SetParam, prefix_refine + 'SLANT_RANGE_DEM_FILE_NAME', workflow_root_name + '_srdem'
  task_refine->SetParam, prefix_refine + 'SYNTHETIC_FILE_NAME', workflow_root_name + '_sint'
  task_refine->SetParam, prefix_refine + 'COHERENCE_FILE_NAME', workflow_root_name + '_cc'
  task_refine->SetParam, prefix_refine + 'OUTPUT_ROOT_NAME', ref_output_root
  task_refine->SetParam, prefix_refine + 'DEM_FILE_NAME', dem_base_file
  task_refine->SetParam, 'REFINEMENT_CMD.REFINEMENT_GCP_FILE_NAME', auto_gcp_shp

  ok_step5 = task_refine->Execute()
  OBJ_DESTROY, task_refine
  IF NOT ok_step5 THEN RETURN, 0
  PRINT, '### STEP 5B: SUCCESS!'

  ; ===================================================================
  ; ### STEP 6: PHASE TO DISPLACEMENT AND GEOCODING ###
  ; ===================================================================

  PRINT, '======================================================'
  PRINT, '### STEP 6: PHASE TO DISPLACEMENT AND GEOCODING ###'
  PRINT, '======================================================'

  final_upha_file = ref_output_root + '_upha'
  geocoded_output_root = workflow_root_name + '_geo'

  task6_obj = OBJ_NEW('SARscapeBatch', Module='InSARPhaseToDisplacement')

  IF (~OBJ_VALID(task6_obj)) THEN BEGIN
    PRINT, '错误: 创建 SARscapeBatch 对象失败: InSARPhaseToDisplacement'
    RETURN, 0
  ENDIF

  ; --- Set Main Parameters ---
  prefix_main6 = 'MAIN_INSAR_PHASE_TO_DISPLACEMENT_CMD.'
  task6_obj->SetParam, prefix_main6 + 'INPUT_FILE_NAME', final_upha_file
  task6_obj->SetParam, prefix_main6 + 'OUTPUT_FILE', geocoded_output_root
  task6_obj->SetParam, prefix_main6 + 'COHERENCE_FILE', workflow_root_name + '_cc'
  task6_obj->SetParam, prefix_main6 + 'DEM_FILE_NAME', dem_base_file
  task6_obj->SetParam, prefix_main6 + 'COHERENCE_THRESHOLD', STRING(geocoding_coh_threshold)

  ; --- Set Other Required Parameters ---
  task6_obj->SetParam, prefix_main6 + 'INTERPOL_TYPE' , '4th_order_cc'
  task6_obj->SetParam, prefix_main6 + 'ALLOW_SKIP_REFINEMENT' , 'NotOK'
  task6_obj->SetParam, prefix_main6 + 'SARSCAPEENVIRONMENT' , 'IDL_ENVI_ENV'

  ; --- Set Geocoding Parameters ---
  prefix_geocode6 = 'GEOCODE_CMD.'
  task6_obj->SetParam, prefix_geocode6 + 'GEOCODE_RG_GRID_SIZE', STRING(geocoding_pixel_size_m)
  task6_obj->SetParam, prefix_geocode6 + 'GEOCODE_AZ_GRID_SIZE', STRING(geocoding_pixel_size_m)
  task6_obj->SetParam, prefix_geocode6 + 'GEOCODE_INTERPOL_BOX_SIZE', '7'

  ; --- Set Displacement Product Generation Parameters ---
  prefix_disp6 = 'DISPLACEMENT_PROJECTION_CMD.'
  task6_obj->SetParam, prefix_disp6 + 'GENERATE_LOS_FLAG', 'OK'
  task6_obj->SetParam, prefix_disp6 + 'GENERATE_VERTICAL_FLAG', 'NotOK'
  task6_obj->SetParam, prefix_disp6 + 'GENERATE_MAX_SLOPE_FLAG', 'NotOK'

  PRINT, '正在执行地理编码任务...'
  ok_step6 = task6_obj->Execute()
  OBJ_DESTROY, task6_obj
  IF NOT ok_step6 THEN RETURN, 0
  PRINT, '### STEP 6: SUCCESS!'

  ; --- Final Success Message ---
  PRINT, '======================================================='
  PRINT, '   FULL D-InSAR WORKFLOW (STEPS 1-6) COMPLETED SUCCESSFULLY!'
  PRINT, '======================================================='

  RETURN, 1

END

;================================================================================
; ### 批量D-InSAR工作流主程序 ###
;================================================================================
PRO Batch_DinsarWorkflow_ALL

  ; --- 初始化SARscape环境 ---
  temporary_directory = 'D:\Sarscape_IDL_Area'
  SARscape_Batch_Init, Temp_Directory=temporary_directory
  e = ENVI(/HEADLESS)
  PRINT, 'ENVI 和 SARscape 环境初始化成功！'
  PRINT, ''

  ; ===================================================================
  ; ### 全局配置 ###
  ; ===================================================================
  rootDir = '\\DESKTOP-N16HJ84\InSAR_Storage_1\Raw_Image_Pool_2025_2'  ; 修改为您的根目录
  dem_base_file = 'D:/SRTM30m/SRTMDEM_RSP_SARscape'  ; DEM文件路径

  ; 处理参数配置
  target_ground_resolution_m = 10.0
  filter_method = 'GOLDSTEIN'
  unwrapping_coh_threshold = 0.05
  gcp_coh_threshold = 0.7
  gcp_number = 100
  geocoding_coh_threshold = 0.0
  geocoding_pixel_size_m = 10.0

  ; 最大处理数量 (0表示处理所有)
  numToProcess = 0

  ; ===================================================================
  ; ### 查找并处理所有Task文件夹 ###
  ; ===================================================================

  pathSep = ''
  IF (!VERSION.OS_FAMILY EQ 'Windows') THEN pathSep = '\' ELSE pathSep = '/'

  IF rootDir EQ '' OR FILE_TEST(rootDir, /DIRECTORY) EQ 0 THEN BEGIN
    PRINT, '错误: "rootDir" 参数未设置或指定的目录不存在: ' + rootDir
    RETURN
  ENDIF

  PRINT, '=============================================='
  PRINT, '开始批量D-InSAR处理...'
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

  ; 循环处理每个Task文件夹
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

    IF (NOT FILE_TEST(masterDir, /DIRECTORY)) OR (NOT FILE_TEST(slaveDir, /DIRECTORY)) THEN BEGIN
      PRINT, '警告: ' + taskName + ' 中缺少master或slave文件夹，跳过。'
      PRINT, ''
      CONTINUE
    ENDIF

    ; 查找master和slave文件夹中的SML文件
    masterSmlFiles = FILE_SEARCH(masterDir + pathSep + '*.sml', COUNT=nMaster)
    slaveSmlFiles = FILE_SEARCH(slaveDir + pathSep + '*.sml', COUNT=nSlave)

    IF (nMaster EQ 0) OR (nSlave EQ 0) THEN BEGIN
      PRINT, '警告: ' + taskName + ' 中未找到足够的SML文件 (master: ' + STRING(nMaster) + ', slave: ' + STRING(nSlave) + ')，跳过。'
      PRINT, ''
      CONTINUE
    ENDIF

    ; 取第一个找到的SML文件作为主从影像
    master_base_file = masterSmlFiles[0]
    slave_base_file = slaveSmlFiles[0]

    ; 移除.sml扩展名，得到基础文件名
    master_base_file = STRMID(master_base_file, 0, STRLEN(master_base_file) - 4)
    slave_base_file = STRMID(slave_base_file, 0, STRLEN(slave_base_file) - 4)

    ; 设置工作流输出根名称（在Task文件夹内创建结果）
    workflow_root_name = taskFolder + pathSep + 'dinsar_results' + pathSep + 'workflow'

    ; 创建输出目录
    outputDir = FILE_DIRNAME(workflow_root_name)
    IF NOT FILE_TEST(outputDir, /DIRECTORY) THEN BEGIN
      FILE_MKDIR, outputDir
      PRINT, '-> 创建输出目录: ' + outputDir
    ENDIF

    PRINT, '-> Master文件: ' + FILE_BASENAME(master_base_file)
    PRINT, '-> Slave文件: ' + FILE_BASENAME(slave_base_file)
    PRINT, '-> 输出路径: ' + workflow_root_name
    PRINT, ''

    ; 执行单个D-InSAR工作流
    success = Execute_Single_Dinsar_Workflow(master_base_file, slave_base_file, dem_base_file, workflow_root_name, $
      target_ground_resolution_m, filter_method, unwrapping_coh_threshold, $
      gcp_coh_threshold, gcp_number, geocoding_coh_threshold, geocoding_pixel_size_m)

    IF success THEN BEGIN
      PRINT, '### ' + taskName + ': D-InSAR工作流完成! ###'
      processed_count = processed_count + 1
    ENDIF ELSE BEGIN
      PRINT, '### ' + taskName + ': D-InSAR工作流失败! ###'
    ENDELSE

    PRINT, ''
  ENDFOREACH

  PRINT, '=============================================='
  PRINT, '批量D-InSAR处理完成！'
  PRINT, '总共成功处理了 ' + STRING(processed_count) + ' 个Task文件夹。'
  PRINT, '=============================================='

  SARscape_Batch_Exit

END