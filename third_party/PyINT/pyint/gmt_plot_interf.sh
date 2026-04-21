#!/usr/bin/env  bash 
# GMT modern mode bash template
# Date:    2024-10-01
#Purpose: quickly generate basic GMT plot
#Author:  <chenweicug@126.com>
#Dependencies: GNUPlot, GMT v6
#Written for: GNU/Linux


if [ $# -lt 2 ];  then
more <<EOF

gmt_plot_interf.sh  is quickly plot the unwrapped phase interferograms and the Los displacement.

Usage: gmt_plot_interf.sh  grdfile satellite 
where 
      grdfile: grd file which contains  unwrapped phase  
      satellite: satellite names: ALOS, S1
      
Example:  
          bash  gmt_plot_interf.sh  s1a_20200530_103025_scn1_unw_ifgramPhase.grd  S1
          bash  gmt_plot_interf.sh  ALOS_PHDR_unw_V10_1089_S1.grd  ALOS

EOF
exit
fi

export GMT_SESSION_NAME=$$	# Set a unique session name
gmt set FONT  14p
gmt set FONT_LABEL  10P 


export  grdfile=$1
export sat=$2
export xmin=`gmt grdinfo  $grdfile | grep 'x_min' | awk '{print $3}'`
echo "$xmin"
export xmax=`gmt grdinfo  $grdfile | grep 'x_min' | awk '{print $5}'`
export ymin=`gmt grdinfo  $grdfile | grep 'y_min' | awk '{print $3}'`
export ymax=`gmt grdinfo  $grdfile | grep 'y_min' | awk '{print $5}'`
export vmin=`gmt grdinfo  $grdfile | grep 'v_min' | awk '{print $3}'`
export vmax=`gmt grdinfo  $grdfile | grep 'v_min' | awk '{print $5}'`

export region="$xmin/$xmax/$ymin/$ymax"
export  color_file=polar
echo "the plot region is $region"
export gmt_basename=`basename ${grdfile}`


if [ $sat=='S1' ]; then
    gmt grdmath  $grdfile  0.0555041577 MUL =  tmp.grd
 elif [ $sat=='ALOS' ]; then
    gmt grdmath  $grdfile  0.236 MUL =  tmp.grd
 else
   gmt grdmath  $grdfile  0.0311 MUL =  tmp.grd
fi

    gmt grdmath tmp.grd  -12.5663704 DIV = los.grd
    rm tmp.grd
   gmt grdmath $grdfile 3.1415926   FMOD =  unw_plot.grd

# GMT plotting

export flag=`gmt  grdinfo los.grd  | grep 'v_min' | awk '{print $5+$3}'`
export vmin=`gmt grdinfo  los.grd | grep 'v_min' | awk '{print $3}'`
export vmax=`gmt grdinfo  los.grd | grep 'v_min' | awk '{print $5}'`
#export  min_num=`echo  "scale=6; 1/$vmin" | bc `
#export max_num=`echo  "scale=6; 1/$vmax" | bc `
echo " flag is $flag   and vmin is $vmin and  vmax is  $vmax "
 if  [ ` echo "$flag  >0 " | bc ` -eq 1  ] && [  ` echo "$vmax  > 1 " | bc ` -eq 1  ];   then
          echo "1"
           export cmin=`echo | gmt grdinfo los.grd  | grep 'v_min' | awk '{print  $5/$3 } '`
           export max_num=`echo  "scale=6; 1/$vmax" | bc `
           export min_num=`echo "$max_num*$vmin" | bc `
            gmt makecpt  -C$color_file  -T$vmin/$vmax/0.01   -G$cmin/1  -Z  >1.cpt
elif [  ` echo "$flag  < 0 " | bc ` -eq 1  ] && [   ` echo "$vmin  < -1 " | bc ` -eq 1  ];  then
           echo "2"
            export cmax=`echo | gmt grdinfo los.grd  | grep 'v_min' | awk '{print $3/$5*-1} '`
            export min_num=`echo  "scale=6; 1/$vmin" | bc`
            export max_num=`echo "$min_num*$vmax" | bc`
             gmt makecpt  -C$color_file  -T$vmin/$vmax/0.01   -G-1/$cmax  -Z  >1.cpt
elif [ `echo "$flag  > 0 " | bc ` -eq 1 ] && [ ` echo "$vmax  < 1 " | bc ` -eq 1   ]; then
            echo  "3"
            if [ ` echo "$vmax  < 0.1 " | bc ` -eq 1   ]; then
            export vmin1=$(echo "scale=2;  $vmin*10" | bc)
            export vmax1=$(echo "scale=2;  $vmax*10" | bc)
            gmt makecpt  -C$color_file  -G$vmin1/$vmax1   -T$vmin/$vmax/0.01 -Z  >1.cpt  
            else   
            gmt makecpt  -C$color_file  -G$vmin/$vmax   -T$vmin/$vmax/0.01 -Z  >1.cpt
            fi
elif [ ` echo "$flag  < 0 " | bc ` -eq 1 ] && [ ` echo "$vmin  > -1 " | bc ` -eq 1 ]; then
           if [ ` echo "$vmin  >-0.1  " | bc ` -eq 1 ]; then 
           echo   "4"
            export vmin1=$(awk "BEGIN{print($vmin*10)}")
            export vmax1=$(awk "BEGIN{print($vmax*10)}")
            echo $vmin1
            echo $vmax1
            gmt makecpt  -C$color_file  -G$vmin1/$vmax1   -T$vmin/$vmax/0.01 -Z   >1.cpt  
            else   
            gmt makecpt  -C$color_file  -G$vmin/$vmax   -T$vmin/$vmax/0.01   -Z  >1.cpt
            fi
else
        echo "5"
             gmt makecpt  -C$color_file  -T$vmin/$vmax/0.01   -Z  >1.cpt
fi

#gmt makecpt  -C$color_file  -G$vmin/$vmax  -T$vmin/$vmax/0.01   >1.cpt
  gmt makecpt -Crainbow -T-3.14159265/3.14159265/0.31415926  -Z  >2.cpt


# Draw GMT map
gmt begin  insar_los_unw_${gmt_basename} png
         gmt subplot begin 1x2  -Fs7.0c  -A+jTR+gwhite+p1p+o0.05c/0.2c -M0.6c/0.25c  -JM7.3c -Bxa1  -Bya1 -BWSen -Y10c -R$region
         gmt subplot set  0  -A"Unw"        
                    gmt grdimage  unw_plot.grd  -C2.cpt  -Q
                     gmt colorbar  -Dx3.5c/-1c+w6c/0.25c+jBC+h   -Bxa3.14     -C2.cpt
         gmt subplot set  1  -A"Los"
                    gmt grdimage  los.grd   -C1.cpt
                      gmt colorbar  -Dx3.5/-1c+w6c/0.25c+jBC+h   -Bxa0.06f0.02     -C1.cpt
        gmt subplot end
gmt end show