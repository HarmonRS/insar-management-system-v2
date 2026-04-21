#!/usr/bin/env  bash 
# GMT modern mode bash template
# Date:    2024-10-01
#Purpose: quickly generate basic GMT plot
#Author:  <chenweicug@126.com>
#Dependencies: GNUPlot, GMT v6
#Written for: GNU/Linux


if [ $# -lt 2 ];  then
more <<EOF

gmt_makecpt.sh: make color file for user defined data. for phase is wrrapperd color cycle and for displacement is defined the blue for negative and red for positive with the defined vmin and vmax.
 The Zero will be white.

Usage: gmt_makecpt.sh vmin vmax type
Where: 
	vmin is the minimum value of color bar.
	vmax is the maximum value of color bar.
	type = los or phase

Example:
    gmt_makecpt.sh -100 300 los  # make color file for displacement plot the result is disp.cpt
    gmt_makecpt.sh -90 180 phase # make color file for phase plot the result is phase.cpt

Author: chenweicug@126.com

EOF
exit
fi

export GMT_SESSION_NAME=$$	# Set a unique session name
gmt set FONT  14p
gmt set FONT_LABEL  10P 

export color_file="polar"
export vmin=$1
export vmax=$2
export type=$3  # type is for dispalcement or phase

export flag=$(awk "BEGIN{print($vmin+$vmax)}")
echo $flag
# GMT plotting
if [  "$type"  ==  "los"  ]; then
   if  [ ` echo "$flag  >0 " | bc ` -eq 1  ] && [  ` echo "$vmax  > 1 " | bc ` -eq 1  ];   then
          echo "1"
            export cmin=$(awk "BEGIN{print($vmin/$vmax)}")
            gmt makecpt  -C$color_file  -T$vmin/$vmax/0.01   -G$cmin/1  -Z  >disp.cpt
   elif [  ` echo "$flag  < 0 " | bc ` -eq 1  ] && [   ` echo "$vmin  < -1 " | bc ` -eq 1  ];  then
           echo "2"
            export cmax=$(awk "BEGIN{print(-$vmax/$vmin)}")
             gmt makecpt  -C$color_file  -T$vmin/$vmax/0.01   -G-1/$cmax  -Z  >disp.cpt
   elif [ `echo "$flag  > 0 " | bc ` -eq 1 ] && [ ` echo "$vmax  < 1 " | bc ` -eq 1   ]; then
            echo  "3"
            if [ ` echo "$vmax  < 0.1 " | bc ` -eq 1   ]; then
            export vmin1=$(awk "BEGIN{print($vmin*10)}")
            export vmax1=$(awk "BEGIN{print($vmax*10)}")
            gmt makecpt  -C$color_file  -G$vmin1/$vmax1   -T$vmin/$vmax/0.01 -Z  >disp.cpt  
            else   
            gmt makecpt  -C$color_file  -G$vmin/$vmax   -T$vmin/$vmax/0.01 -Z  >disp.cpt
            fi
   elif [ ` echo "$flag  < 0 " | bc ` -eq 1 ] && [ ` echo "$vmin  > -1 " | bc ` -eq 1 ]; then
           if [ ` echo "$vmin  >-0.1  " | bc ` -eq 1 ]; then 
           echo   "4"
            export vmin1=$(awk "BEGIN{print($vmin*10)}")
            export vmax1=$(awk "BEGIN{print($vmax*10)}")
            gmt makecpt  -C$color_file  -G$vmin1/$vmax1   -T$vmin/$vmax/0.01 -Z   >disp.cpt  
            else   
            gmt makecpt  -C$color_file  -G$vmin/$vmax   -T$vmin/$vmax/0.01   -Z  >disp.cpt
            fi
  else
        echo "5"
             gmt makecpt  -C$color_file  -T$vmin/$vmax/0.01   -Z  >disp.cpt
  fi     

elif [ "$type" == "phase"  ];  then
          export inc=$(awk "BEGIN{print($vmax/10)}")
           gmt makecpt -Crainbow -T$vmin/$vmax/$inc -Ww -Z   >phase.cpt
else 
    echo "error in the type"
   exit
fi
export  inc=$(awk "BEGIN{print(($vmax-$vmin)/4)}")
export  inc_c=$(awk "BEGIN{print(($vmax-$vmin)/4)}")

# Draw GMT map
