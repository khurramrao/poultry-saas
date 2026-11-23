"""Restore missing RayNoor Supplier Returns patch-source folder.

This utility ONLY restores the files needed by the existing installer.
It does not edit any Django code, database, inventory or accounting entries.
"""
from pathlib import Path
import base64
import io
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parent
FOLDER = 'RAYNOOR_SUPPLIER_RETURNS_PATCH_FILES'
DATA = (
    'P)h>@6aWAK2mrT;cU{MRTfY+o004Rs002G!002@!Sx!$+QeRV5P*6-sMN(f<MO0N%PE%h{K~zIXUq(qxMN=<LNk>vaR7p=xUsOd+'
    'P)tEoMJ{l8E_8Tw?N`li<2De!`zZ)rYy(<#cl&eLLWf=3L4!7%G;vNrK+xh?W<!x$l5*lL3iKiRgng0@Ma#AV+i{W&uq~>Ce#n`R'
    'ABV&Fs3?jqs<M;<3^Cv;qby;H6T5DlR*KsnNe&7ObHE9kI_z*WAW~;cl0xLzA+#d+EN5LyB-&X=B(P}`MNvl-r8bUKmEfJJ(FNhZ'
    'F_r5i(`rhVofe!EDz_~IK54pFgV>l9nuD|r>dG_;?Frt%Ti!FMoWM>e3izxQbUK-2){>J3x3?Z4SvB_GbqHP@N>ChBCcu)EP&yo)'
    'jXn-X9|y;y3rcGnUBc2GEw1V&e{G-+3}9SNJjvdw-(QLqkVd0CKRdl>R^z;0j3{myP6bGA)2mjazj~42Q4^TtgUH`kP9J&eUTH+Z'
    '%!1y)A|0CwVvMvAOp4Et9x-X5-;cN8Iei0a4S9KaJPddormT`K^^2MK+jlVmE*zW4C16Y{B|cS954RmXq#ETIkM$Ts{A!P}b>)&e'
    '>%8lB>`xU_PPoMy_)J^j#2k*5gB(m4W4kjlcQn)1ZP0=YBPfGc!R{y*xo|dpgA?bWkCu{UVACJAg9<eV<uoqBoXHBF1pRh9bhA%s'
    'X8a<2QE1$d5(;F#MjgzUSwl)txtpbLU&kbyY2mT1dfmW;D(#>fcURRVG!x%UdiY*tKuWgoY18la<6bvZ8p<p0c3yuFR+dK3vF7~!'
    '=U;@~GT}>vck{Hm{p(%X#jI8ju>Q8q8nZ@X(%{g)hBxvJrm?L;V*AR*p{B`M!E)eKE6R)!xT5GY42=Y9VAJSrZ;e(t6%GotIaZlA'
    '1~Nx*PB?vsBUs#pwI$pk4~tf#`h}F0$!5%kPK~wJVBe{K5|$f)R)zL^X=gB28G`WzgEb5u2+SM5o)gK`&7KYHm(i|)#&G8X&_j+{'
    'T?9F0uD$WcxVr-a!_tM>b4wZLdw_(cb1gV*);+CJa#Xv9?5T0ypB|Jf-@iT}CV|eLnXLh0qADp52-9Svj2&qMB3D1dqUUtl=w<T$'
    'a{Lp$ycnI+lhfhoml%_f))ENmPH9Ze&QHgq!8pe3k@Y}9gD{tvkaQzw>LUSN(uwv?{43$Q%8EyXTWYt9d&EkSK%SQ<B266+=Fi}M'
    'pY)EvHdWvNZX2dA&#N&zfY*lU^VfP8tLyfsF6#qV`g^1e$HR`wqj%&gdey2u(JMSMUH`H6+`xR^p)F|FfA{DXWcnqJZmiIi`l5%J'
    '<xzna;AWaX@*u<P3|04p2}K6}-D~j#YYKISDLbt-`2&8VkPkgEulhSwxORI<1%A40{jbRX0!3c_FfISa@tOJ3_Eie<J*rQU<9jmn'
    '2$@+qJZl~`^A}Z_>&`z=O9KQH000080Jn&DT{@AVJ5~e$01*!W080P>08&9&PESu#UsF|3P)tchQeRR<R8>+=Q(sU)R6|H#MoCOX'
    'Q!im~X)kSWWMyn~FJ)(EUvO`8UvqVEaBOL1a$j;~bairWb1ras?N-}v<2Dd|&sVT|k^#r{Znygoz(BLHf(FgSwH*{iu@D$>B(tGN'
    'ElDNu76tYr_6z$Z9g37MTIbSDuus*4Es-;4hBJpV6GhQvu{1oRf|$Ch1P4tt*jh`|Ck862sRfd2RgzoHEvP&<gi1!LT4xJtfMiN*'
    '$ShaV45BF7Yh)Q@yhKc1R!Unk_Md*Qt~qNr-20oL@+Xyr8e~f5?XCf9xh%|<0K00pOT|EJZ`PIz)9zbd!bc@xZ!Z(n7?Kpl<YG1p'
    '$DB5&j6KNU%>DHa_6R<+WIjJl&XV&v8IeDlCHKTprh##rN@XGHlc+@^ex0~=661TF0jcPcUJ2+$z5WJ*hP;+6Wpx8EZN3PtDy?pS'
    'k$*mYCiIqacW4Jtg0!vv`{s9=9L_GzXC1#j)KqZ0bdbOLSMoinrK!1fK9E9F2HTD>TG9eos$@#F<~LM0?Xh1aN|JVUOY2rES)@pV'
    '(n+7lkb8pu<Do5DHT+}865M5AjFV5Aw-s}?#gtuuc2XDah0sC+I@00fb}+2E28p2vDZIQq86R^HEUqR0P{U}hYv>~tb6N{KYN*yH'
    'E>kWjqhl%z^co<c>>B1+Y2bzY1(tC{D?ae7IgvM*45gQ+%A%JV1l|jlN?O7wYQ0Ru%|QW-E24pgQGlIHFXqY7+#6h7o=2=s+E6{)'
    ';wq4jSZq1{g+(+(xp=ZYFPAb`Jys^i_~kRyCZ)Zi1EH`4IccwsjF#5%c%?g02JgiUoJb1=Xb<CsG2J%Jl-WpPC^%lKq<1Of1-EAO'
    '>P^3`O=%@4@^SRmgYY)NDYCwXuY!8L9qTmIh37zF1r*@wz#8RfLG>C!fGq4{^m^DQ*+OxryRKhtE~ruR5r7{y078}NoCa$bXpk8^'
    'g7nJyawVv|Zf>AQakbH`73Xkgy<%4oW7e?lYPplDZA%y&Ue13`FK5X#J-Zkur}y*m9wK*dP#O$J+ip<1P2V{Z);msXtFRT^rj)S_'
    '|G7zT6lYkwR(kK05<ulP^Eb+mlTV+Nq#uVnLhH>v;Hj*I2*vOo-4_h@PGe8afp-T};B(M)?K6};STzcz8EDrYu>(f?>nyp>vCmob'
    '-mCtE9jO9sm(3T(oUGW3rxJN@jp%KYJer<QlllH%ZhKH&9}M5JzazqP5*7#^6G)IcPkxIYu;hBYhLUoLu67N!kQ;aVyAjzO4e*p+'
    'WJRX6_J+%$oW#*^FdPQnZfqlO;*Ll^Snd?4eZ1sl9N!ISscLj5NBehJ5xul`%nWiaEa-UUQ=l+{cC7Sz4^3Qt=-D-QO%8u=hc2A8'
    's9CtAo?#Hlk^4YHadd^0O0A~S<bGUuhYL7&j9n#l<H`$<1|5p-ye7L&`6c6<AU>gQdKP?T-wX+SM#l+bXIg??DE1$^PRO1|Y0~tP'
    '4ddzM@LM&IFyVy6;(z99)BpYB8}^S*3SOdn{0mS^0|XQR000O8w}^LLR~N5js|)}DXe|H$O#lD@QbAcxPft=`Q&mt<Oi4vjUs6R>'
    'RZ>n<Ur<3*Lr7mnNlZmkFJW+LFLPybc4=c}b1!9QXJ2q{b6<0HaBysCWpZC~Wps6NZgVbhdCgi`Z`?Q%e)q57Y969a+DdycI}c72'
    '?Cit=f|*TbZD)W*5D09E-Da#76_K*j8~DF(6)#aoAIWBc%|qPHqgYj}`s!duQFPtVO43|$QPGmTdj0Axk!@X<oHe9js%<Jso^(Tb'
    '!wRnC(}u0us*uk}C5WUYBLe2AydkBKGKr$-WYvfbDX3zKZy4d5S~N<3w=*o~8#vKUyfk0u<aKpNOJ2OKSwoR2v$}10;g-I8_xj~r'
    'kNjjweWcY|B*oILP@PP@6|Lf>^oxM4e~ZepLu;OFM8Qg#u=P5tg<p5EUjOm-3fN+m63zB^p?2-aoR%`DxE_fsJA0`o=aZwCTCLpy'
    'k~MccXELFAF4{`*YVC-Yykc{*0SUHq0;j7C7;Lr^dmVcl{+Sh-6m0{ej!aGSTR#en<1SVJ#Rl%9*=8I*+eu!?3F@LkPBM8TYDd#i'
    '$v(9dcvjn!laqq2NT#_(Uby0SEQXtIL53A)WidNHA@C0*Fdy(=K~f`NY|SH<sxiA}vzcDTKIg1fBp#a0oLp~fcF{DVnUg<phyFb4'
    'h&Pl=7EHyf=!@p$foxi-$dVCCG*Ln~sQV-`NAeXg_B<tvucGCIc2taskiw}(iCb314ZWXIgk6l5uT_A{`to;Y2a@lTHnJK6+Mikm'
    'JZc6Ka>+_T3!-iqS&3$Y>{<@dck$#oDe01xyDkiuC1@sG3Zy{Q4wk>+f-Wir804<yi{|5@cP}rzUnix=X{nh$aO;K)z*kiXeOJ6P'
    '^0%P%ofk$W7`e)5vEfw=VB>iQK9G(cLRj=)5-u~^0%h=t9QZ26vpJfG8;<?BRCKk%;clb_OK*XLy?6Y!Dz~7nQmogkI0dmZw;b++'
    'B6AzM1+j9`XinfSd{HC#4@}@A00D#vi*K=5ew@RqJJv{+zM`cBXVx61Z{PwfXAlcKc*5O7@E{b>i&h0FAP3xfW`l}oAgbhy-_a$+'
    '6WTFyO4kiz-VIMQ*_V8^VhyWuxGo8FYgS2IBS~AKOu!U;T{VoaZ`4x>8o$Nx1?Q0%?**>GNvP@F(S}o?HhV^J@2RD+Me35(6g0-{'
    'E?(tg1EN+aIec4{`Z6&L%EZa6|3Y+7)PoqPE8$KRnT9cPN^sN%4x<TUaB!yDqC@dq#rjL8z-7|Nt&Zkq4FGf6qk@=iW-7X2y}1NO'
    'tRF(S;1CE>^4^;eLU|nhxL7PAv}3)KDOIu!tR`tnBEw+@_Q@gw!H<#X?Cj?S@KUfPSLuW}b5GuY1e1BgYeeZZy6Ptu5L)U6^j-kH'
    '$B(rPv-yC#Ghh#<&4zSBsq@|u;NZB%g`x+0>2P6syc8;dBa1dyiBuxL&Dsj&tIM{b<wImo|KJfu-{^}F{H-1nV4YP(Y?%Bx)QR5I'
    '=PG&^SZUx4<mu4=;QpPZM-D+t&&l(Hocweas_S%$m#e{v-uCGd{t~SgqE+iSfb*^2k52E|lfSh_+`o@<NSC4yPAg_evxS3}K|QJ$'
    'z+m5SM#gigS)k=Pi6BRXoynT1I11K);88T2pcD9$-`9=VZJm1!*6^a6)h;%e$1y`=);k*FA+m`9#%jw?8w>05xY*Cw?CX|o!Io_<'
    'cRolp&%Zo!_wgvW9OIym9zAGNuWuisdGETmI;f{pA#+3pDVW3brg@N@So2KH5(mpqNF1GBUi=x2NcFB{!!{y~?On2=^-pZ})>?yJ'
    'ZYT`dr878k6_Fo|uyzveDwNtPvk#<JiCJUqY2r}7)YG+xj!0WDu5YzTV`Rvd+tg<RIM<EOwRgzWT7w}r5pJ<6h|QnF5%;SChas8_'
    'RMCqiyS&F({{H<(#hWu1Z!EEzZZY0u8>VhVVZsj7mOkDr&N^(hTTE`S8`p3VG3f;>qH#<yaP2VYIGPqKHR)WGB_vN1f$PIDfcaf9'
    'ayL|BmeSXWH-!UGxbu|n0Fe{0I6}iiAc(RY1VLwBJskW-IJC6Pp8+C{fN>RFzPY@3b=q-ozW5n%=~#>mp>j1c!7n>}(4YU{E?b}H'
    '&MA&N^Z;UN@hy>kxY^qf?K-6cq+R%Fr*}dqg6H3Y22cW(&W7h2P))7ls+Y&GQz)G*yX0_AhN&y*(q9B#Dn&Kr3!1$-3FD(Fr~`_f'
    'IYeOim_SSTWHEcHoHX?{QUQpt4FdhY&%vfzNC`w3Kq8r}cnQ_A*^!!k3DjRw$-M<}bbSLQk2S4(sHbQNau-{uZvZ-<k)nmeuzUr{'
    'mQdtNP%>G9tK71})ylo5z$YDyc%@>e=Krz}5dG8A2^ux`Hv)>rs@*Ih=5!4KJo`Ya#x)h7CWY%&1RTFB5ddk>=sr?U)Yd8KsF^jg'
    '9Pg*DeziF6nF}f_Ecbb3BKG8^UR_*Y|9<h0i#OMMHS_C@5HNHDV2T|K5BB%GEbV!E!XiWzWb<g+hxxd?_~q*Do2z4(pk0VW+U_u6'
    '`~x*;I!d9>M6Tw6Z&S9k#LNr47Iu&OvflDVRc;~9n5s@kA{}|^+tfIG-~J{Dy%bEo5$XcHCA7+8duvw=lL6nT@`)-In^vrKBib5x'
    '0ZF=6YwYApI_AytAIKF1OeAoMu10hVhpo5R@W95yQ}9$p*X$fJkxByFXjS?Qu-^iFLQ{e5m~NCXAMeqh)a+*vmb11t@z0@&7rJ9G'
    '%XpP`E#lZoHtTQ|ux~?o#jACetyQ|Pd2XUv0}PKNyrh}q5m>C)XBA^c6eb6neKb}`CF)zu3lUZ9ox*WC2M&91NXs_Uv{Jj80EclX'
    'h;HOBkY~CjQMfNih7Wezyrllt3tK?vv_h=%*rEg&h|iNdR}4ADR^OfA1uOO=Huenpn&T6WH#Wp>vo|2_yzjX_#wEmhY~Kl&om;v-'
    'X^oo;W|C^~BeBxR(n-A*qL2eDexZ*Yo5kJ+t=8+8>5UJhzS&BMM<v0&jkNkVEnJ9WfyXIPP>e)V>78q<tU2}`!9(a~Sx_{zb8T%s'
    'MPWoS#3*F`F)9Psu+KHODI_sA+Z%deH+g-y<IsQuG%@burAom>Hs}Zw!cX~zrwZdi_rcov_aUq0)=ua_0G)9Px2&uKp-xR;9zPUy'
    ';Bnr|vL+x_`;^X93=Pe-d5kiUC!fI_#&mrhtF~NW5H!p68<8X{-bimBE-DW+<#?TbD2oR|A7oo>TtiJe3<z-b+dGtlmS~U40E0Qq'
    '%HbG91BZ)qZ((?{?p0#nuq&~*J~r;i3r*_WA$gD>>WmnJ<eUUpnGUcs$Z755WVav)?5S`8-d%M13A1g_eu|&Et%HVcYWn9|{>OM7'
    'vr(d+4qMG#a+Qu#_|#_dQ^|Qe9z*+qO#S`&*^i5$;5I3Itp9XN7K_D)ap~eZJ2t{idEIG>qWKk=ZpSA4X8J8rrthQiw&;T)I((ba'
    'rqFx2GS9<DR7bkm^w?K3c;T)U((la7Nm%~6g2&cEOIqFbYM$?2>6pjz?^W;UHcnXY7=3in6V5s0T2qOW9RnyrgNVB(oKH%<p1UZG'
    'j~XuWyRKnSE?}nORNu1b@DpG~?9-yP1$s}p{xJa^HiUGVV#A%mCwu2XBp>wW4iyddp8ojdU&q!jzv3@RK<vMR4Pl0hi3JGI($aBR'
    'FbVu?7<Y^r8=a`kobg765xa0_ITBcO<WB3#kzNip=!x&opgg7ErFqG~R>G3ZJfC-GS)pFoqinb3n5=2s{B%GAcZwAfxs>J!Y(#dR'
    '{J44u%-&+c4(lJd;ok5vXa}YC1=*{-3lv81)ZSfJcJxdy9pcddbmIQ^K929j=wa`@(?SnZyWzdSsykvmWYrRDWObb*3`mC{(pKpy'
    'iR~vNPy*E;uj8(Qz+aLWf?%X)$8<lyp%mpMD>`#AZz1l?_-yfnKnBnNJ1{Zpeim`OX?zVl`uCM_4>6m9P^(bG)K`OakIb-<8X~h@'
    '(F5ll?boA9H>07a4wilBF&)~zl7mxIa^18noHG*{RbhowiTijqv1*A)qPDByWadrGe0YEUSIB3=Rg-y%=M3-5vl4t+ZrvAq!bILO'
    '`N)NGx;L(#Cj_rANBN%X`G#4||DW$!sgCo#@12pr4MFB<|74jhYmQD-j%Xn4GH^@E{;Jfaa-Onz>O3x;a}UW>7kj2`w{~I+zB$I*'
    '%0E=7uHWt0IoM&`gCX`uC83sfB>YcE_`{TOZ^ZDvH(fG`1QeqR(+E$>{BFawgmhQRDngf@ftZtgr9H;JwIb;!u>oS?!~wVpmJG1U'
    '&Fldxoh<*)P-^_kxBU<WOHcj-P)h>@6aWAK2mrT;cU?KLuPUwt001!&002t>002@!Sx!$+QeRV5P*6-sMN(f<MO0N%PE%h{K~zIX'
    'Uq(qxMN=<faA_}db#QQOX=QR>aCLHHXkl|@Uvg!1b#iWVUvy=2baO6ndCgZ%Z{s!)z57=PJjn}Nh!e*s0thJD=CHuFStMz%1T9Sz'
    'Z75PDDW`7H|K1@*%90%0n@tv|4u&}z&c~ZKLy8b`+lc77G>=zNy)}<gL=_XPfTZ3=2DFJ-T_(DzYhHq)b)&LJrhzIjjS`w#6-@}4'
    'JSbU3Ib>zUcvM!kR3`eRUAFI<rp&_&`+w=Td|{%HNhXC+<vL-Fc??<w7}{v`TFD&vK;1>=)$Z~C&luA{p)n=bLBo_4{4Isoun9Sf'
    'I)S2~we0k+i{kUm-Df59CNnd?yz{w-^=A*kc>U2p6=Wa9285An`$?KZPH6|K$8s1&aC_@s?u;NFn=*sbW)x&n<z715Egu?@+vNt7'
    'tK)cmz#Ypn*$7jLA~+CIVR5N{LD!5kk-=m#$vD$G8YZA^m2@0xHNBcd_~h^qX)wRnu?GI&h4!(_#MVAaM>L41iClky%;+RjzzkRb'
    'Kv{*@{s>k<O4>9WJ3*0I1QjcJO6>Ya_={CF2c%quZ)%M78Ief|n{z3Oxz(8mDVYvK3SeVc!!nJ6irX;S+#N$ZJjN#LmTsLzPruU!'
    'QC(vK`l&(r{crR8>%05;@)AWEZYYp_DM6C#`SX0;IrM_D$B=6}$Q%(1GayH;H1t-&WyZJ_#Z!#HnaJsSn|7rf#lht+=YGG8!{~O|'
    '$AgvjkwXY1h6L1XYlJ9-XQ4TfK{^0rHt>Yu(F_UK!}in7J#mc$<py&*lK!6!!&Wg{8kE!X#o5q)PX)Otu&>g^YBh@*QCi2^q<(tF'
    '<ot)^!aZcVv#~>Se+-QwJdp1JG(%+jF&}?kj{M9$7`a6>4V9lxy8gHQMi=L2e)LcdYy+AuSJa+LvO$|nKT^#&P^suM(*}>H@xZ{0'
    'kxhe{7uyn+dFL}J^Imf3aYqn}K;@-L$;HL$3_~Wz^Tv^7I8;d4l(ajkkOmw%n3f_X=!T;gl&}nWy=#KwhCKw1S=y-hC3Yc_Mm~XX'
    '3hp39*?w;&IN22-aCVf_d>xNeaKM2w*EZ0&+p|x~S+rao*@>_xWoTZyVZ1?Fb>+WUo;ft9CoGK0(6`|0amqqBlWdI#EXS&Ut@mzk'
    'kR0Fp^7)6Pw|ut;i$WmVnG_6m;y}y3r(W9gugUw6|J7pU`mM9J45o%X*B<?}29aZ-DZ~G3CZ2*^T4QhTvX`6ebv@=I%A5aDi;Y?2'
    'W9&6fpTwhD)@*At>tktm$38fO(=`?%$tqji)RHRkv~pW%1vnh>*5XQL6chTv^6TWh4p_cA|2>?)9gxKyAa>OE&D^q5gZ>q?wzK{Z'
    'J6Eoil6ZC#Dfl+J)uczuHm^4VX-}D>_<axLhS%`eyTGu0bV>cR&+o1Ue!|*Ed4t_$Z+sEzt8IiZAQq;Sg1jeXhaU(p0emRRaPh@!'
    'DUKA*A;&KZylvpM$af?8j>i{Xs>Xv)NZNrGZXK>_ZU$mp;ah4v{+tpvZNIqPOQs5)=4EEycOt&;9DLREbH9BC^vC{(4>sX%>R`q('
    '_Iw*@yy)cz`}nP1ZtR>`TrN*J_^0(TBQ9NXHRxOt>PD^>mzV!eC;tIZO9KQH000080Jn&DUHt_wANvac07xVN08{_~08&9&PESu#'
    'UsF|3P)tchQeRR<R8>+=Q(sU)R6|H#MoCOXQ!im~X)knTZE$R1bY*idVQ^_LWoKt!aBp*8aCLHHXkl|@Uvg!1b#iWVE@*UZY_(c@'
    'liRit|NlM(%JeEJ(es@1Loe5H?1`so;+#93$#gmjM1m4xlAr<b&Z?}>)&Kj+x{C)NI_mDyIx`+yBo>Q({&pAi{+zInnq`q99h&nI'
    '+iX_3P^&dnY`E2F(jn)E{W(bljmWl0X~zG>;MEztTPGrXPyCKhWWYz`+Fa>f!j@-4$@Cx=GTld9<q6$QH<Cv){A-|D3U4$Ugd!=@'
    'Oid-r8P(m3e#*0ye(a9>lUVk8vklFslZ*W0!Eg(yjP`39zTZeuWYP4CIF4s)AtNTIll&u5BH<DFB6`NYW|3PMNE-1%O(&1@kF%Uc'
    '5zjW$$y0bpsod~vuoha2bUOLEehS$>+S~Q%n2ZUMgQ!AIzZla;PcNU<DD%Brxtu(vPoh~2rWhzNR84R+14-zbC8aTp{XA1(dGUB%'
    '-wNv3#~a2sTRnX`9v=yOSu!t@WN*xBtvQ{H1{1rqWl6q2;_9V+{AB#}YfGjy)lIvO<EQaun`tlDW%TUWw3E7NOQkhkCv1PiBfXtY'
    '#^ZCp3j|1LuGrN7Xf>JHP4u>3e~R}0m}hXL<L4b%Ny#o4jmI+$!9Ab}-(=H-#d>zlq~;+_+&eAu=280=)2wy2cV@^$#*Q@~qZl>A'
    'jh{Tbyrka77@o&YEbWByV$jXPLMoun1&17x_E;vRkX{~oartO+X&2H$v*?sdpBTB%{+zOi(=O!Ez*-;9%|G_`RYtZaA*5X0bG1WY'
    '`vEe0<WhSc@oN$$RH;S4@_}NZ<{}$tt`in4adkQ0K3dMXCyMF7x(0qoq47}k2+)rBlDsSOJmE}|w}lM1fIZ}j=|W~|K0+?}(ms;h'
    'Ek_7DiU>&*qX|6zN~Iz<TyI68iDV)82uw^UQLt?2G=kL)g+Sl1;e3?C!A442Qv+Nbkga6#A^?0VWI{R~-gtHm=qBV20Wny<@sINn'
    'b({e9tQ9;%j}O7}j1aWOm66M-Mzko^Hd-PPNTswC*=AW<BrTLCTYAk1-AD$i!aluaB$VJ{jjDmX5P3uNjoC_Gm-=QxlO5B9h9S!#'
    'Z2AQCP?~SR0c6b}6bvV-t+isF(JbUNK^=HHB<~Cxnngr;fq)l8x>kh3-Xf2HQ$$1-GIFzJ85sPUMaICWkb%9FfnTCU0Cv<t-8#4$'
    '5ROaEvDP-P@%<895Gon3w0IAE&ku8+<=|bt%h@8>@+e~9t&FDdM2+DCa!r$h8Q#DJV(at{!?fJ6-xGVv;b6Q4lGg&<Lo6^f2)=w<'
    'hT&T&q9W8}L|(sn{UiAv?EU3@WT_w?5{L+QU?TL2M^;-a9}owP975^<-1(v4;N0b0<R+Y)jKT8dRtUw6_r1NKFzflq5~9JtqWB8j'
    'jQ=RKT9XTC3Jb&hJ*rB;d61Xr<8tLgK{H6>-Ab~Q@(kmmqzu7<C7~;0D|~54;CLPrs|Jf!D8^#pPWuG039QiJR8Kn!v&d7zK4?H-'
    '>-k~n#j|D|qUnG)`Om+}(TaLsG#Ajmp@@#ieblyaYE|^sF=(4rk#W7slR`pb08P1}Zh24vjSmJNS;ago_-OLM^OHN5*eHM?sv^e5'
    's|^@0Up-`g!pACP40+(<J1f#PlL3MLX%S2UKmeATA3mT6xGQ8^kwE!^?ZeHGOfJR{)GDP(QV(`)8tTWsxTZY8iV!T{*_fs#$^eQK'
    'W0gHfzTGLTuNHm~l92T&OL&YMIAn8>HgbqYx>u@q!Z_ZqE4!yI>`V9NA!Zs6$EBt0?{;IoND=ZH7-;bHr12{Jc1opmM<B*ycoR#3'
    'tzZ_xDP>t9m_|eLQe-g)$iz}s9(SJ|T|q%4K4C6vnH5r;G>l!W17bvXpYZLp^Yb7bL>-gm&{N#CT%p&UvO>vG07@sY(yrbKjt=XL'
    'xP8L|G0$#Xg<HWx){cJWm%({Vb+hqs_)Up?vMhWjZmJNu;Sd&zX=sR^pVHGVrw{@EgRa%)&#v!@n6{aPO$FvW;3FJa>YLLYBE-xm'
    'Z&bL>0*Giu2E7^M*QVWujN*x@W>CHh01da3E7!r9KH@<L;<L<%Hgd>{{Fr3WncNK_C0R>qK(r?;+vqJIDPUz273%f?3ku0$I0HS_'
    '=z51UrzC$70RVx~l)Ay5@HlO4O8Vup&B0Bp>g_FYOZ1`z0I-1Wo@v+z^93lV2j$KOhqOyqR8f^8;#39AA9Yfh#kw$ly=lJj80;s-'
    '4bWU18S=`a0k)YiNoC>&TKbI%Z3~v(RVcS-exC}&kF~+_g(l7dwo5~(?GtQ+Q`5XwAPkjEK>Io+t_`o36>MIYSBgNL7?-{F@yTX5'
    '3zmP|LJ-3r?7uUJc%`W1$<743SEi$U)KXJjhU5of#+AMwQ!uDPs<s4$GS=YA<y1u0mYWN8t|52hp_^&$b?&-=fw?iLn9;{ZohsHT'
    '7EtKv6ONssJ0rBUsoU{Is#)z(>Z+$X9vMuzbP{8hkF4&`JH5cqMP)L(p!*X|^BFuK(AYs_KC*&W8qP=9QZ8{YwZqq4V;Ih0$A^5!'
    'l^x^kocWIBRAL=9aaVTPAfL2cmC-DsDfnk}BRR~mB90wksbQsrRTKP0GZ?f0Wnr=h3vA(ULSuL^#z;lRcIZ|MfyvGRWH~|^2b!UK'
    '5kM5kI1x8f%x+M}-dfbu)V4>P17`{qSWEo5{FWL}ykaqA&(^#ur5yPQrd)y+v;6(0(Vg7hZGBk1FDp{{>V-4f2oR|x9id<&FXUQ8'
    'J5baWIWTC7)opA2f@R655n=hl#bK?NaqUM${z`tv_G7-5<p5ogJ9IZ?&!?>+%xTt=yME*C`d~T0E|flM2z5MXv`Nx3*c=IDN?o12'
    'Hq=y=@aSkiC%Eb5)=_OEOvwcB%<m~taAjJCPY`KTyMgWE6VEi%la!v=eAJIWV6)%3VR}_-W!zK=ly;o7qN&xj;MgypTg$HNels^^'
    'fFiH{{_5)8t8W1$mbH<x#-=(32h)zt&=oW)9h1}u)~(0=SZKiG%-krs9;K$bV^a=6r**GMRhq$|w3V^td=%>;qPGe{mSl`aMNGr^'
    'rXaowkVEb~-a1u0;6Q-6^_E+k|LZaakGb<Q#-%h~k5rAT(aGLZ{HBYXx-F_p9p84DQhU^F-7_gVt<27obE6-N+C9;njO^hE%MTO8'
    'PG;Uz*7KivSo7VU4Fv#TE!syGX!A{;+URH*TWZrveJ(Rn+f66ul;1d|Sn<B0+53As{WXx>)#bPM<e#@Ae^}4eOwC%mW4avZdR!{k'
    'ki-K>T;2qu!A%%qB3`qV??ey^TD#TK?H1MCeX<SsCYoW5x#qM+h49^si5i5HZbeuNTv+M3PPGM_pvh6Jw7~C$N#>N*gRsfS-NYuR'
    'QxlzT`M!CX(KhjPC*!XOw3rg)1A%(A2@c|_8l8fB5aPbDY+hzH!n`F!n`1!p^T;g+-5sJfXYVd@?Ip^ef|cR-4}*KZ7d*5?eU>0-'
    '`o89%JI^?F`f+IdE<s@3b5{@~3jf2v?K`ETb{m6X<*o(Gd-`&PMJuXnU2qcsZ8wEu1-?&nU7H(VbV;yhUj$ErB~IAYfM&ihmUm6~'
    'p={XXRMi|irNF;9VU-{J>O*HJd9IgdUC0XauG`zgz^pW0lrJI?76_3Dm#=Ie_s#B2)a{gCf;zqa2fe$G5QZ}#v_1BHwDxui$axlv'
    '`-saMeq@hddG2ElG@agz$K5}Gem(ngcIK6bsW%Br3y6jJ6yrYk{Ag;r`p5X6{cQ1r>9M+?`p`}tpT@m@E_ulOl6XGEZ%@zw4^c2f'
    'V82-1CNS@jVum^o@v7*h{ckCYV=D5T{PM@W`FW@=GH^;#weJ~_A{wu9K>lB*ox8t%7+#$js)B8olcm#{nVbE(EFGw`ov*stmqyXv'
    'P>b&coX*~vZ)DvL%LZ@%(&_IZLwQ=Hucd{pZ}|qKn_l#RecSs(Z+19COVcQNg|poEFhjEpOw<8%$u_Lh@AekUz4gru$WhkY#0FG4'
    'eI)D69P!zqhkrxFyEwP$VgCyd<luh+P)h>@6aWAK2mrT;cU=gNiZzc0000>k002q=002@!Sx!$+QeRV5P*6-sMN(f<MO0N%PE%h{'
    'K~zIXUq(qxMN=<faA_}gX=QhFFJ)(EUvO`8UvqVEaBOL1a$j;~bairWb1ras%~(rs+c*%u`&SU^MFO%+_i=JzpatqK3Ut#VNf%fY'
    'g+NQR%|;Scl5*-T^51ucBB_U^IJvEXAQERd&yT|yMNxFq)O8`aB8r<vRhOCKxiFWCZ<{J7pWnYCn%7J*!^u|3lH{ypJ5Cr7H7hu|'
    'm+Ed?$a_trC|a~EJQF1=h$w5R40+qWwzQ@ZxhLTBA8yMxR_!FsWM!1tc!rW|4a)Qs4`q$nQ&1(rHmS7VQ^=jDQpLYF0z4V)%r~@@'
    'IWGe5y8&76VJ&M+AY&vH&&&lpRXJA!?#75tkP#*SS5|z$aS$A$(pgCN#?+l`Yha3NY01rA=FUx43uh+fyIoq#pk=+=efoIwzB=#<'
    '<Q+dqGq)qvE~{yq)sYCF^J`1Im|VTK0O%BGt_~vO=T*cD0zZXr=Z<#Ky`oTjJ#eLYkL=$qeUU5{i=1ys%JNcFaRbVerIU+0$VSS-'
    'E>odXL;{3FWB}6KA~R4jBMvQC12@>s6}}Lu0;4u;yG>B->k)unPh~3p68*WpjV?$8al4JaCf3@zQQ4jaw{(nKxi-#pu_cu>-X}++'
    'J>T%>Ay2tdQh6>=^sy?A1hf&mU1;NAU$P_oRd^<SMuH(OT5vRzpuh#tV>^y~K69q`8_84-%-&vaWa~Crt(?Q1y)Vccv<EnU>7_x)'
    'iU{pNo|7$vniWtHJa#Wg&5mqSaE;07N!zgM?lJ>Y2zXFoAjmr=ibio#Hrm(-mW{!3#Hyp&i)u&1b{FA(Xlb_R*<D&Qt)WuoA?)Y|'
    '-Er{W#xpQrPNSrkkVlj9z5-Zd7>8LQUfL_L$2&sqIXb~|-cQG)DH#K*w-1pGL0*w5k1Q=p5a8ep1*=3IcYBRG4*i3;d3liGR#`M;'
    '!JpiG1BHW=9iK4auaG7c`RdfNmJlTu<a@&aD&{!B<VnCbDLoXP_NtnYFyfdM#EmI<35cAM5r}B6;!MkGf~_UYX=sB4N6YZZy@7hx'
    'SE!5tpDN>zxf__~#KGy{dP0I-M?V1uQmy(2Xj+hwcYG=wc6~~Fa<UQ*m2NV)S3<R%R=ACR0{m$3@L@^pf(Oe3X)0l`<dpKj;owCx'
    'VnUG|Hac0sL`kl7D|G~*<puq1`{0nza~hp-j~C+;g*wikDCCMC1JdN73N)L*QZ|!#ZVB^1Mcwm24OG+tr5n$My9_7zhYWK%vD80`'
    '>mkFOm~&8k5~iO4Pry*GXSU$+uxVpFLJN^#{J7+wBBb8{J%W@zTkP^Og9=A`&as}r#hJSIyuh_hC3#_^Ro^{$hjMZ+%$~3Y4jb9S'
    'H3}`l(?fRVy6UsVPd`ZilaP(dUWgdXWkzOq@Q%^KqBIVd=BF`HOh8-sH!B*xw!KXBxw@<5ebqIOt{Z71-IvOLWW0v{zS})LIncL~'
    '>nmy2KLolkKU@qL6T&pAcv|NLElmu}$%%Dq`wuK1bvR)-gyw#?7&a?*4RiDX9=VFsv|=Ss(}Z#_EgsLwq*fAB1v|+W8dRQ-;y>Fn'
    'ey*_Zg|bC5S=I%IP8Ry+ar`N{l2B-s5im#(I!Mh9!)!mNeO$QqOvA}2AnNhyOi2N7AV$-zC=5g+=+P47@zfk^zKXnT=$C_<6b7k-'
    '6j1Y4?-;<Z>+-;Yl0ko5?b38-RxjJ51f3}_Kow969NpmJP%>{agH5=P2R;X73B{7C(#<jOw#h;+aGt}o=k6(3z=Ql{`TNV4U)x~e'
    '6av1(6k<crAtZPp3@;%v00m|MVZhbXZmA&m3I_cy`oPn*3)q9CwSqYrqzv{^OUfjSFk(AW2$K?yEhyI>TPrj04>aXM*9AMaf>u}_'
    '+}D+O#*So3|JXjX4T~$R!_<$?u223bGr@@IyudTI_GvGF&^h5wNLA#LU1Jq9*}hA6dq-)^(yxOoVd;h}IfJ`Ixmt)9o4ww_75fpX'
    'H{95J*pB_~mPDw3sjb)=g?2JDg_$%?{McC2fZwl+c0Rxuz~!_GBPC4zVUlS_#W5#VCjV+h!<ZW5qLTM;<t2D`4SCQqAbMCPEe4`!'
    'F*w}#0jgI;SS>E+&N<zivH<ITc3DHg(bCF?%`UF6;GXcjIeSv{Y#BW2E<I?da)x{S$1YmPj1_p;Vz@6|zqRxU>22&rsWjV5{D=f>'
    'ljyry-`;*$|Fyol?QL)G&2w9?*Kck<Ufm4#S)jO2HZ%L&P4wx_Kb_WxWbq$RO928D0~7!N00;oLh<9Dbep|m21ONbf5dZ)_00000'
    '000000001h0RR9108&9&PESu#UsF|3P)tchQeRR<R8>+=Q(sU)R6|H#MoCOXQ!h<PM^ZslNl#8+R7FitOhHsdE^v7+ba-@7O928D'
    '0~7!N00;oLh<9B&k)S(P1ONaL4*&p500000000000001h0dE8V08&9&PESu#UsF|3P)tchQeRR<R8>+=Q(sU)R6|H#MoCOXQ!im~'
    'X)kSWWMyn~FJ)(EUvO`8UvqVEaBOL1a$j;~bairWb1rasP)h*<6ay3h000O8w}^LLR~N5js|)}DXe|H$O#lD@0000000000fB`NE'
    '002@!Sx!$+QeRV5P*6-sMN(f<MO0N%PE%h{K~zIXUq(qxMN=<faA_}dWpZ|DV`XzMWoKt!aBp*8b9HcVY-we3Uvg!1b#iWVE^v8J'
    'O928D0~7!N00;oLh<9B%v9Btw1ONar5dZ*700000000000001h0Ynx608&9&PESu#UsF|3P)tchQeRR<R8>+=Q(sU)R6|H#MoCOX'
    'Q!im~X)kkiaBysCWpZC|b#h~9VRL0)a%FUNa&B{9bY*jNb1rasP)h*<6ay3h000O8w}^LL{RJ-{`wIX7NF)FNQ~&?~0000000000'
    'fB{<@002@!Sx!$+QeRV5P*6-sMN(f<MO0N%PE%h{K~zIXUq(qxMN=<faA_}eWo>Y5VRU74FJW+LFJ)(EUvO`8UvPDDV`yP>WnXe-'
    'bairWb1rCfZER3W0Rj{Q6aWAK2mrT;cU=gNiZzc0000>k002q=0000000000004ji$R_{*QbAcxPft=`Q&mt<Oi4vjUs6R>RZ>n<'
    'Ur<3*Lr7mnNlZmkFJW+LFLr5VcXKagXJ=n<Z*yOBb#QQOX=QR>a%FUNa&B`jaCuNm1qJ{B000I61_09n006%(00000'
)

def main():
    if not (ROOT / 'manage.py').is_file():
        raise SystemExit('STOPPED: Place this file beside manage.py in your poultrysaas folder.')
    installer = ROOT / 'APPLY_RAYNOOR_SUPPLIER_RETURNS.py'
    if not installer.is_file():
        raise SystemExit('STOPPED: Original APPLY_RAYNOOR_SUPPLIER_RETURNS.py is missing. Extract it from the original ZIP.')
    raw = base64.b85decode(DATA)
    with zipfile.ZipFile(io.BytesIO(raw)) as arc:
        items=[]
        for entry in arc.infolist():
            if entry.is_dir():
                continue
            # ZIP member paths use forward slashes on every platform.
            # Converting to Windows Path before checking the prefix incorrectly
            # turns '/' into '\\' and rejects valid entries on Windows.
            member_name = entry.filename
            parts = member_name.split('/')
            if (
                not member_name.startswith(FOLDER + '/')
                or len(parts) < 2
                or parts[0] != FOLDER
                or any(part in ('', '.', '..') for part in parts)
                or '\\' in member_name
                or ':' in member_name
            ):
                raise SystemExit('STOPPED: Unsafe embedded patch path: ' + repr(member_name))
            relative = Path(*parts)
            dest = ROOT.joinpath(*parts)
            # Prevent path traversal even when running on Windows.
            if not dest.resolve().is_relative_to(ROOT.resolve()):
                raise SystemExit('STOPPED: Unsafe destination path: ' + repr(member_name))
            content=arc.read(entry)
            if dest.exists():
                if hashlib.sha256(dest.read_bytes()).digest() != hashlib.sha256(content).digest():
                    raise SystemExit('STOPPED: File differs; refusing to overwrite: ' + str(relative))
            items.append((dest,content))
    added=0
    for dest,content in items:
        if dest.exists():
            print('Already present:', dest.relative_to(ROOT))
            continue
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(content)
        added+=1
        print('Restored:', dest.relative_to(ROOT))
    print('\nSUCCESS: Restored', added, 'missing file(s). No Django application code was modified.')
    print('Next run: python APPLY_RAYNOOR_SUPPLIER_RETURNS.py')

if __name__ == '__main__':
    main()
