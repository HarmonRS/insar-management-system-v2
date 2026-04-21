#!/usr/bin/env  bash 
if [#agrv < 3]; then

 echo"
      Usage: geotiff2grd.sh a.tif b.grd
        a.tif is a geotif file include nan data
        b.grd is a grd file translate by gdal without nan data in it
        
        Example: geotiff2grd.sh  20240114-20240126_los.tif 20240114-20240126_los.grd


      Chen Wei 2024/2/17
"

exit

fi

export input=$1
export  out_grdfile=$2
gdal_calc.py -A  $input --outfile=tmp.tif  --calc='(A==0)*(-9999)+(A!=0)*A'
gdal_translate  -a_nodata  -9999  -of GSBG  tmp.tif  $out_grdfile
rm tmp.tif *.xml
